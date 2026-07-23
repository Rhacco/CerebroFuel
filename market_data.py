"""Public exchange-candle confirmation for v3.3.3.

LiveCoinWatch remains the broad, low-credit full-pool source.  This module adds
optional five-minute candles for the detail candidates from public exchange
endpoints.  Binance quote volume and taker-buy volume are preferred; Coinbase
candles are a price/volume fallback.  Any provider failure is non-fatal.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import requests

PURPLE = "🟣"
GREEN = "🟢"
BLUE = "🔵"
YELLOW = "🟡"
ORANGE = "🟠"
RED = "🔴"
WHITE = "⚪"


@dataclass(frozen=True)
class Candle:
    open_ms: int
    close_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    taker_buy_quote_volume: float | None = None


@dataclass
class IntradayMetrics:
    display: str
    provider: str = "none"
    symbol: str | None = None
    candle_count: int = 0
    coverage_hours: float = 0.0
    data_quality: str = "insufficient"
    exact_interval_volume: bool = False
    taker_flow_available: bool = False
    price_changes: dict[int, float | None] = field(default_factory=dict)
    volume_ratios: dict[int, float | None] = field(default_factory=dict)
    volume_z: dict[int, float | None] = field(default_factory=dict)
    volume_acceleration: dict[int, float | None] = field(default_factory=dict)
    taker_buy_share: dict[int, float | None] = field(default_factory=dict)
    volume_colors: dict[int, str] = field(default_factory=dict)
    demand_score: float = 0.0
    sell_pressure_score: float = 0.0
    base_quality_score: float = 0.0
    room_to_target_score: float = 0.0
    overextension_penalty: float = 0.0
    falling_knife: bool = False
    late_entry: bool = False
    range_position_180: float | None = None
    range_position_1440: float | None = None
    distance_to_24h_high_pct: float | None = None
    distance_above_3h_low_pct: float | None = None
    new_3h_low_age_minutes: float | None = None
    quote_volume_24h: float = 0.0
    latest_candle_open_ms: int | None = None
    latest_high: float | None = None
    latest_low: float | None = None
    latest_close: float | None = None
    reasons: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PublicMarketDataClient:
    """Bounded, best-effort public candle loader.

    Discovery requests are made once.  Candidate candle requests are parallel
    but request-count bounded.  No API key is used or accepted.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        section = config.get("market_data") if isinstance(config, Mapping) else None
        self.config = section if isinstance(section, Mapping) else {}
        self.enabled = bool(self.config.get("enabled", True))
        self.timeout = float(self.config.get("timeout_seconds", 12.0))
        self.max_requests = max(0, int(self.config.get("maximum_requests_per_run", 36)))
        self.workers = max(1, min(8, int(self.config.get("parallel_requests", 4))))
        self.limit = max(72, min(300, int(self.config.get("candle_limit", 300))))
        self.binance_base = str(
            self.config.get("binance_base_url", "https://data-api.binance.vision")
        ).rstrip("/")
        self.coinbase_base = str(
            self.config.get("coinbase_base_url", "https://api.exchange.coinbase.com")
        ).rstrip("/")
        raw_order = self.config.get("provider_order", ["binance", "coinbase"])
        self.provider_order = tuple(
            str(value).lower() for value in raw_order if str(value).lower() in {"binance", "coinbase"}
        ) or ("binance", "coinbase")
        self._request_count = 0
        self._request_lock = threading.Lock()
        self._last_request = 0.0
        self._spacing = max(0.0, float(self.config.get("request_spacing_seconds", 0.03)))
        self._diagnostics: list[str] = []

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    def _reserve_request(self) -> bool:
        with self._request_lock:
            if self._request_count >= self.max_requests:
                return False
            wait = self._spacing - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._request_count += 1
            self._last_request = time.monotonic()
            return True

    def _get_json(self, url: str, *, params: Mapping[str, Any] | None = None) -> Any:
        if not self._reserve_request():
            raise RuntimeError("Public-market-data request cap reached")
        response = requests.get(
            url,
            params=dict(params or {}),
            timeout=self.timeout,
            headers={"Accept": "application/json", "User-Agent": "crypto-signal-monitor/3.3.3"},
        )
        if response.status_code == 429:
            retry = response.headers.get("Retry-After")
            raise RuntimeError(f"public provider rate limit (Retry-After={retry})")
        response.raise_for_status()
        return response.json()

    def _discover_binance(self) -> dict[str, str]:
        try:
            raw = self._get_json(f"{self.binance_base}/api/v3/exchangeInfo")
        except Exception as exc:  # best effort
            self._diagnostics.append(f"Binance discovery unavailable: {exc}")
            return {}
        symbols = raw.get("symbols") if isinstance(raw, Mapping) else None
        if not isinstance(symbols, list):
            return {}
        quote_priority = {"USDT": 0, "USDC": 1, "FDUSD": 2, "USD": 3}
        chosen: dict[str, tuple[int, str]] = {}
        for item in symbols:
            if not isinstance(item, Mapping) or str(item.get("status")) != "TRADING":
                continue
            base = str(item.get("baseAsset") or "").upper()
            quote = str(item.get("quoteAsset") or "").upper()
            symbol = str(item.get("symbol") or "").upper()
            if not base or quote not in quote_priority or not symbol:
                continue
            candidate = (quote_priority[quote], symbol)
            if base not in chosen or candidate < chosen[base]:
                chosen[base] = candidate
        return {base: value[1] for base, value in chosen.items()}

    def _discover_coinbase(self) -> dict[str, str]:
        try:
            raw = self._get_json(f"{self.coinbase_base}/products")
        except Exception as exc:  # best effort
            self._diagnostics.append(f"Coinbase discovery unavailable: {exc}")
            return {}
        if not isinstance(raw, list):
            return {}
        quote_priority = {"USD": 0, "USDC": 1, "USDT": 2}
        chosen: dict[str, tuple[int, str]] = {}
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            status = str(item.get("status") or "online").lower()
            if status not in {"online", ""} or bool(item.get("trading_disabled", False)):
                continue
            base = str(item.get("base_currency") or "").upper()
            quote = str(item.get("quote_currency") or "").upper()
            product = str(item.get("id") or "").upper()
            if not base or quote not in quote_priority or not product:
                continue
            candidate = (quote_priority[quote], product)
            if base not in chosen or candidate < chosen[base]:
                chosen[base] = candidate
        return {base: value[1] for base, value in chosen.items()}

    def _fetch_binance(self, symbol: str, now_ms: int) -> list[Candle]:
        raw = self._get_json(
            f"{self.binance_base}/api/v3/klines",
            params={"symbol": symbol, "interval": "5m", "limit": self.limit},
        )
        if not isinstance(raw, list):
            raise RuntimeError("unexpected Binance kline response")
        candles: list[Candle] = []
        for item in raw:
            if not isinstance(item, list) or len(item) < 11:
                continue
            try:
                candle = Candle(
                    open_ms=int(item[0]),
                    close_ms=int(item[6]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    quote_volume=float(item[7]),
                    taker_buy_quote_volume=float(item[10]),
                )
            except (TypeError, ValueError):
                continue
            if candle.close_ms < now_ms and candle.close > 0 and candle.quote_volume >= 0:
                candles.append(candle)
        return sorted({item.open_ms: item for item in candles}.values(), key=lambda item: item.open_ms)

    def _fetch_coinbase(self, product: str, now_ms: int) -> list[Candle]:
        seconds = self.limit * 300
        end_s = now_ms // 1000
        start_s = end_s - seconds - 600
        raw = self._get_json(
            f"{self.coinbase_base}/products/{product}/candles",
            params={"granularity": 300, "start": start_s, "end": end_s},
        )
        if not isinstance(raw, list):
            raise RuntimeError("unexpected Coinbase candle response")
        candles: list[Candle] = []
        for item in raw:
            if not isinstance(item, list) or len(item) < 6:
                continue
            try:
                open_ms = int(item[0]) * 1000
                low, high, open_, close, base_volume = map(float, item[1:6])
                quote_volume = max(0.0, base_volume * ((open_ + high + low + close) / 4.0))
                candle = Candle(
                    open_ms=open_ms,
                    close_ms=open_ms + 300_000 - 1,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    quote_volume=quote_volume,
                    taker_buy_quote_volume=None,
                )
            except (TypeError, ValueError):
                continue
            if candle.close_ms < now_ms and candle.close > 0 and candle.quote_volume >= 0:
                candles.append(candle)
        return sorted({item.open_ms: item for item in candles}.values(), key=lambda item: item.open_ms)[-self.limit :]

    def fetch_many(
        self,
        displays: Sequence[str],
        *,
        now_ms: int,
        aliases: Mapping[str, Sequence[str]] | None = None,
    ) -> tuple[dict[str, IntradayMetrics], dict[str, Any]]:
        if not self.enabled or self.max_requests <= 0:
            return {}, {"enabled": False, "requests": 0, "providers": {}, "diagnostics": []}

        alias_map = {
            str(display).upper(): tuple(str(value).upper() for value in values if str(value).strip())
            for display, values in (aliases or {}).items()
        }
        binance = self._discover_binance() if "binance" in self.provider_order else {}
        coinbase = self._discover_coinbase() if "coinbase" in self.provider_order else {}

        def choices(display: str) -> list[tuple[str, str]]:
            bases = alias_map.get(display.upper()) or (display.upper(),)
            result: list[tuple[str, str]] = []
            for provider in self.provider_order:
                source = binance if provider == "binance" else coinbase
                for base in bases:
                    symbol = source.get(base)
                    # Discovery is preferred because it avoids wasting calls on
                    # non-existent markets.  If a provider's discovery endpoint
                    # itself failed, try one canonical direct symbol so a
                    # transient discovery outage does not disable all candles.
                    if not symbol and not source:
                        symbol = f"{base}USDT" if provider == "binance" else f"{base}-USD"
                    if symbol and (provider, symbol) not in result:
                        result.append((provider, symbol))
            return result

        def load(display: str) -> IntradayMetrics:
            errors: list[str] = []
            for provider, symbol in choices(display):
                try:
                    candles = (
                        self._fetch_binance(symbol, now_ms)
                        if provider == "binance"
                        else self._fetch_coinbase(symbol, now_ms)
                    )
                    metrics = analyze_candles(display, provider, symbol, candles, now_ms=now_ms)
                    if metrics.candle_count >= 24:
                        return metrics
                    errors.append(f"{provider}:{symbol} only {metrics.candle_count} candles")
                except Exception as exc:  # best effort per coin
                    errors.append(f"{provider}:{symbol} {exc}")
            if errors:
                self._diagnostics.append(f"{display}: " + " | ".join(errors))
            return IntradayMetrics(display=display)

        unique = list(dict.fromkeys(str(value).upper() for value in displays if str(value).strip()))
        result: dict[str, IntradayMetrics] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(load, display): display for display in unique}
            for future in as_completed(futures):
                display = futures[future]
                try:
                    result[display] = future.result()
                except Exception as exc:
                    self._diagnostics.append(f"{display}: unexpected candle error: {exc}")
                    result[display] = IntradayMetrics(display=display)

        providers: dict[str, int] = {}
        for item in result.values():
            providers[item.provider] = providers.get(item.provider, 0) + 1
        return result, {
            "enabled": True,
            "requests": self.request_count,
            "providers": providers,
            "exact_count": sum(item.exact_interval_volume for item in result.values()),
            "requested_coins": len(unique),
            "diagnostics": list(self.diagnostics),
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _pct(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _percentile(values: Sequence[float], fraction: float) -> float:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return 0.0
    position = _clamp(fraction) * (len(cleaned) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return cleaned[low]
    weight = position - low
    return cleaned[low] * (1.0 - weight) + cleaned[high] * weight


def _robust_z(value: float, samples: Sequence[float]) -> float:
    cleaned = [float(item) for item in samples if math.isfinite(float(item))]
    if len(cleaned) < 4:
        return 0.0
    centre = statistics.median(cleaned)
    deviations = [abs(item - centre) for item in cleaned]
    mad = statistics.median(deviations) * 1.4826
    iqr = (_percentile(cleaned, 0.75) - _percentile(cleaned, 0.25)) / 1.349
    scale = max(mad, iqr, abs(centre) * 0.08, 1e-9)
    return max(-6.0, min(6.0, (value - centre) / scale))


def _window_count(minutes: int) -> int:
    return max(1, int(math.ceil(minutes / 5.0)))


def _window_price(candles: Sequence[Candle], minutes: int) -> float | None:
    count = _window_count(minutes)
    if len(candles) <= count:
        return None
    return _pct(candles[-1].close, candles[-count - 1].close)


def _window_sums(candles: Sequence[Candle], minutes: int) -> tuple[float | None, list[float], float | None]:
    count = _window_count(minutes)
    if len(candles) < count * 4:
        return None, [], None
    volumes = [max(0.0, candle.quote_volume) for candle in candles]
    recent = sum(volumes[-count:])
    samples: list[float] = []
    end = len(volumes) - count
    while end - count >= 0 and len(samples) < 36:
        samples.append(sum(volumes[end - count : end]))
        end -= count
    if not samples:
        return recent, [], None
    baseline = statistics.median(samples)
    ratio = recent / baseline if baseline > 0 else None
    return recent, samples, ratio


def _taker_share(candles: Sequence[Candle], minutes: int) -> float | None:
    count = _window_count(minutes)
    selected = candles[-count:]
    if len(selected) < count or any(item.taker_buy_quote_volume is None for item in selected):
        return None
    total = sum(item.quote_volume for item in selected)
    buy = sum(float(item.taker_buy_quote_volume or 0.0) for item in selected)
    return buy / total if total > 0 else None


def _volume_color(ratio: float | None, z: float | None) -> str:
    if ratio is None:
        return WHITE
    z = float(z or 0.0)
    if ratio >= 2.8 and z >= 2.2:
        return PURPLE
    if ratio >= 1.55 and z >= 0.8:
        return GREEN
    if ratio >= 1.12:
        return BLUE
    if ratio <= 0.42 and z <= -1.2:
        return RED
    if ratio <= 0.78:
        return ORANGE
    return YELLOW


def _range_position(candles: Sequence[Candle], minutes: int) -> tuple[float | None, float | None, float | None]:
    count = _window_count(minutes)
    selected = candles[-count:]
    if len(selected) < max(3, count // 2):
        return None, None, None
    low = min(item.low for item in selected)
    high = max(item.high for item in selected)
    close = selected[-1].close
    if high <= low:
        return 0.5, low, high
    return _clamp((close - low) / (high - low)), low, high


def _new_low_age_minutes(candles: Sequence[Candle], minutes: int = 180) -> float | None:
    count = _window_count(minutes)
    selected = candles[-count:]
    if len(selected) < max(6, count // 2):
        return None
    index = min(range(len(selected)), key=lambda position: selected[position].low)
    return (len(selected) - 1 - index) * 5.0


def _log_ratio_score(ratio: float | None) -> float:
    if ratio is None or ratio <= 0:
        return 0.0
    return 100.0 * _clamp((math.log(ratio, 2.0) + 1.0) / 3.0)


def analyze_candles(
    display: str,
    provider: str,
    symbol: str,
    candles: Iterable[Candle],
    *,
    now_ms: int,
) -> IntradayMetrics:
    ordered = sorted({item.open_ms: item for item in candles}.values(), key=lambda item: item.open_ms)
    if not ordered:
        return IntradayMetrics(display=display, provider=provider, symbol=symbol)
    coverage_hours = max(0.0, (ordered[-1].close_ms - ordered[0].open_ms) / 3_600_000.0)
    quality = "good" if len(ordered) >= 220 and coverage_hours >= 18 else (
        "partial" if len(ordered) >= 60 and coverage_hours >= 4 else "insufficient"
    )

    price_changes = {minutes: _window_price(ordered, minutes) for minutes in (5, 10, 15, 30, 60, 180, 1440)}
    volume_ratios: dict[int, float | None] = {}
    volume_z: dict[int, float | None] = {}
    volume_acceleration: dict[int, float | None] = {}
    volume_colors: dict[int, str] = {}
    for minutes in (5, 10, 15, 30, 60):
        recent, samples, ratio = _window_sums(ordered, minutes)
        z = None if recent is None or not samples else _robust_z(recent, samples)
        volume_ratios[minutes] = ratio
        volume_z[minutes] = z
        count = _window_count(minutes)
        if len(ordered) >= count * 2:
            previous = sum(item.quote_volume for item in ordered[-2 * count : -count])
            volume_acceleration[minutes] = recent / previous if recent is not None and previous > 0 else None
        else:
            volume_acceleration[minutes] = None
        volume_colors[minutes] = _volume_color(ratio, z)

    taker = {minutes: _taker_share(ordered, minutes) for minutes in (15, 30, 60)}
    pos180, low180, high180 = _range_position(ordered, 180)
    pos1440, low1440, high1440 = _range_position(ordered, 1440)
    close = ordered[-1].close
    age_low = _new_low_age_minutes(ordered, 180)
    distance_high = _pct(float(high1440), close) if high1440 else None
    distance_low = _pct(close, float(low180)) if low180 else None

    # Demand: current interval volume relative to its own recent distribution,
    # acceleration, and (where available) aggressive-buy share.
    ratio_score = (
        0.42 * _log_ratio_score(volume_ratios.get(15))
        + 0.36 * _log_ratio_score(volume_ratios.get(30))
        + 0.22 * _log_ratio_score(volume_ratios.get(60))
    )
    acceleration_values = [
        float(value) for value in (volume_acceleration.get(15), volume_acceleration.get(30))
        if value is not None and math.isfinite(float(value))
    ]
    acceleration_score = (
        statistics.mean(100.0 * _clamp((math.log(max(value, 1e-6), 2.0) + 1.0) / 3.0) for value in acceleration_values)
        if acceleration_values else 50.0
    )
    buy_values = [float(value) for value in taker.values() if value is not None]
    buy_score = statistics.mean(100.0 * _clamp((value - 0.38) / 0.24) for value in buy_values) if buy_values else 50.0
    demand = 0.62 * ratio_score + 0.20 * acceleration_score + 0.18 * buy_score

    p15 = float(price_changes.get(15) or 0.0)
    p30 = float(price_changes.get(30) or 0.0)
    p60 = float(price_changes.get(60) or 0.0)
    p180 = float(price_changes.get(180) or 0.0)
    p24 = float(price_changes.get(1440) or 0.0)
    negative_votes = sum((p15 < -0.22, p30 < -0.45, p60 < -0.85, p180 < -2.0))
    fresh_low = age_low is not None and age_low <= 20.0
    falling_knife = bool(
        p30 <= -0.85
        or p60 <= -1.55
        or p180 <= -3.2
        or (fresh_low and p15 < -0.05)
        or negative_votes >= 3
    )

    ideal_low_position = 1.0 - min(1.0, abs(float(pos180 or 0.5) - 0.34) / 0.46)
    slope_stability = 1.0 - _clamp(abs(p180 - 0.45) / 4.0)
    short_stability = 1.0 - _clamp(max(abs(p15), abs(p30) * 0.65) / 1.4)
    low_age_score = 0.45 if age_low is None else _clamp((age_low - 15.0) / 90.0)
    base_quality = 100.0 * (
        0.34 * ideal_low_position + 0.28 * slope_stability + 0.22 * short_stability + 0.16 * low_age_score
    )
    if falling_knife:
        base_quality *= 0.18

    room_from_high = 50.0 if distance_high is None else 100.0 * _clamp((distance_high - 0.8) / 5.2)
    range_room = 50.0 if pos1440 is None else 100.0 * _clamp((0.92 - pos1440) / 0.70)
    room = 0.62 * room_from_high + 0.38 * range_room

    overextension = 0.0
    if p60 > 3.0:
        overextension += _clamp((p60 - 3.0) / 4.0) * 35.0
    if p180 > 6.0:
        overextension += _clamp((p180 - 6.0) / 7.0) * 35.0
    if p24 > 10.0:
        overextension += _clamp((p24 - 10.0) / 15.0) * 20.0
    if pos180 is not None and pos180 > 0.88:
        overextension += _clamp((pos180 - 0.88) / 0.12) * 20.0
    decelerating = all(
        value is not None and float(value) < 0.82
        for value in (volume_acceleration.get(15), volume_acceleration.get(30))
    )
    late_entry = bool(overextension >= 35.0 or ((p60 > 2.3 or p180 > 4.5) and decelerating))
    overextension = min(100.0, overextension + (18.0 if decelerating and p60 > 1.5 else 0.0))

    # Selling pressure includes a falling price with active volume and a rising
    # price that has lost volume support (failed/unsupported run).
    ratio30 = float(volume_ratios.get(30) or 1.0)
    buy30 = taker.get(30)
    active_sell = _clamp((-p30) / 1.5) * _clamp((ratio30 - 0.85) / 1.6)
    unsupported_rise = _clamp(p30 / 1.8) * _clamp((0.95 - ratio30) / 0.65)
    seller_share = 0.0 if buy30 is None else _clamp((0.50 - float(buy30)) / 0.15)
    base_break = 1.0 if falling_knife else _clamp((-p60 - 0.35) / 1.8)
    sell_pressure = 100.0 * _clamp(
        0.40 * active_sell + 0.24 * unsupported_rise + 0.16 * seller_share + 0.20 * base_break
    )

    if falling_knife:
        demand *= 0.15
    elif p30 < -0.25:
        demand *= _clamp((p30 + 0.85) / 0.60)

    quote_volume_24h = sum(item.quote_volume for item in ordered[-288:])
    reasons: list[str] = []
    if demand >= 70:
        reasons.append("echtes Intervallvolumen beschleunigt")
    if base_quality >= 70:
        reasons.append("stabile 3h-Basis")
    if falling_knife:
        reasons.append("Falling-Knife-Sperre")
    if late_entry:
        reasons.append("bereits weit gelaufen")
    if taker.get(30) is not None and float(taker[30]) >= 0.58:
        reasons.append("Taker-Kaufanteil hoch")

    return IntradayMetrics(
        display=display,
        provider=provider,
        symbol=symbol,
        candle_count=len(ordered),
        coverage_hours=round(coverage_hours, 3),
        data_quality=quality,
        exact_interval_volume=True,
        taker_flow_available=any(value is not None for value in taker.values()),
        price_changes={key: None if value is None else round(float(value), 6) for key, value in price_changes.items()},
        volume_ratios={key: None if value is None else round(float(value), 6) for key, value in volume_ratios.items()},
        volume_z={key: None if value is None else round(float(value), 6) for key, value in volume_z.items()},
        volume_acceleration={key: None if value is None else round(float(value), 6) for key, value in volume_acceleration.items()},
        taker_buy_share={key: None if value is None else round(float(value), 6) for key, value in taker.items()},
        volume_colors={key: value for key, value in volume_colors.items()},
        demand_score=round(_clamp(demand / 100.0) * 100.0, 4),
        sell_pressure_score=round(_clamp(sell_pressure / 100.0) * 100.0, 4),
        base_quality_score=round(_clamp(base_quality / 100.0) * 100.0, 4),
        room_to_target_score=round(_clamp(room / 100.0) * 100.0, 4),
        overextension_penalty=round(_clamp(overextension / 100.0) * 100.0, 4),
        falling_knife=falling_knife,
        late_entry=late_entry,
        range_position_180=None if pos180 is None else round(pos180, 6),
        range_position_1440=None if pos1440 is None else round(pos1440, 6),
        distance_to_24h_high_pct=None if distance_high is None else round(distance_high, 6),
        distance_above_3h_low_pct=None if distance_low is None else round(distance_low, 6),
        new_3h_low_age_minutes=None if age_low is None else round(age_low, 3),
        quote_volume_24h=round(quote_volume_24h, 4),
        latest_candle_open_ms=int(ordered[-1].open_ms),
        latest_high=round(float(ordered[-1].high), 12),
        latest_low=round(float(ordered[-1].low), 12),
        latest_close=round(float(ordered[-1].close), 12),
        reasons=tuple(reasons),
    )
