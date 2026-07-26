"""Fast public exchange data for crypto-signal-monitor v3.5.

The short-horizon engine uses closed one-minute candles. Binance is preferred
because its klines include quote volume and taker-buy quote volume. Coinbase is
an unauthenticated fallback. LiveCoinWatch is intentionally not used here for
short interval volume because its map volume is a rolling 24-hour value.
"""
from __future__ import annotations

import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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
    interval_minutes: int = 1
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
    cheap_price_score: float = 0.0
    stabilization_score: float = 0.0
    recent_drawdown_pct: float = 0.0
    rebound_from_low_pct: float = 0.0
    discount_qualified: bool = False
    stabilized_after_drop: bool = False
    confirmed_recovery: bool = False
    room_to_target_score: float = 0.0
    overextension_penalty: float = 0.0
    falling_knife: bool = False
    late_entry: bool = False
    range_position_180: float | None = None
    range_position_300: float | None = None
    range_position_1440: float | None = None
    distance_to_24h_high_pct: float | None = None
    distance_above_3h_low_pct: float | None = None
    new_3h_low_age_minutes: float | None = None
    quote_volume_24h: float = 0.0
    high_24h: float | None = None
    low_24h: float | None = None
    price_change_24h_pct: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread_pct: float | None = None
    top_book_depth_usd: float | None = None
    execution_quality_score: float = 50.0
    execution_data_available: bool = False
    latest_candle_open_ms: int | None = None
    latest_high: float | None = None
    latest_low: float | None = None
    latest_close: float | None = None
    reasons: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PublicMarketDataClient:
    """Bounded public one-minute market-data loader with non-fatal fallbacks."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        section = config.get("market_data") if isinstance(config, Mapping) else None
        self.config = section if isinstance(section, Mapping) else {}
        self.enabled = bool(self.config.get("enabled", True))
        self.timeout = max(4.0, float(self.config.get("timeout_seconds", 12.0)))
        self.max_requests = max(0, int(self.config.get("maximum_requests_per_run", 110)))
        self.workers = max(1, min(10, int(self.config.get("parallel_requests", 8))))
        self.limit = max(180, min(300, int(self.config.get("candle_limit", 300))))
        self.binance_base = str(
            self.config.get("binance_base_url", "https://data-api.binance.vision")
        ).rstrip("/")
        self.coinbase_base = str(
            self.config.get("coinbase_base_url", "https://api.exchange.coinbase.com")
        ).rstrip("/")
        raw_order = self.config.get("provider_order", ["binance", "coinbase"])
        self.provider_order = tuple(
            str(value).lower()
            for value in raw_order
            if str(value).lower() in {"binance", "coinbase"}
        ) or ("binance", "coinbase")
        self._request_count = 0
        self._request_lock = threading.Lock()
        self._last_request = 0.0
        self._spacing = max(0.0, float(self.config.get("request_spacing_seconds", 0.025)))
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
        last_error: Exception | None = None
        for attempt in range(3):
            if not self._reserve_request():
                raise RuntimeError("Public-market-data request cap reached")
            try:
                response = requests.get(
                    url,
                    params=dict(params or {}),
                    timeout=self.timeout,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "crypto-signal-monitor/3.5",
                    },
                )
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After")
                    try:
                        wait = min(8.0, max(0.5, float(retry or (attempt + 1))))
                    except ValueError:
                        wait = float(attempt + 1)
                    last_error = RuntimeError(f"public provider rate limit (Retry-After={retry})")
                    if attempt < 2:
                        time.sleep(wait)
                        continue
                    raise last_error
                if response.status_code >= 500 and attempt < 2:
                    last_error = RuntimeError(f"provider HTTP {response.status_code}")
                    time.sleep(0.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                raise
        raise RuntimeError(str(last_error or "unknown public market-data error"))

    def _discover_binance(self) -> dict[str, str]:
        try:
            raw = self._get_json(f"{self.binance_base}/api/v3/exchangeInfo")
        except Exception as exc:
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
        except Exception as exc:
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

    def _binance_book_tickers(self) -> dict[str, Mapping[str, Any]]:
        try:
            raw = self._get_json(f"{self.binance_base}/api/v3/ticker/bookTicker")
        except Exception as exc:
            self._diagnostics.append(f"Binance bookTicker unavailable: {exc}")
            return {}
        if isinstance(raw, Mapping):
            raw = [raw]
        if not isinstance(raw, list):
            return {}
        return {
            str(item.get("symbol") or "").upper(): item
            for item in raw
            if isinstance(item, Mapping) and str(item.get("symbol") or "")
        }

    def _binance_24h_tickers(self) -> dict[str, Mapping[str, Any]]:
        try:
            raw = self._get_json(f"{self.binance_base}/api/v3/ticker/24hr")
        except Exception as exc:
            self._diagnostics.append(f"Binance 24h ticker unavailable: {exc}")
            return {}
        if isinstance(raw, Mapping):
            raw = [raw]
        if not isinstance(raw, list):
            return {}
        return {
            str(item.get("symbol") or "").upper(): item
            for item in raw
            if isinstance(item, Mapping) and str(item.get("symbol") or "")
        }

    def _fetch_binance(self, symbol: str, now_ms: int) -> list[Candle]:
        raw = self._get_json(
            f"{self.binance_base}/api/v3/klines",
            params={"symbol": symbol, "interval": "1m", "limit": self.limit},
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
        end_s = now_ms // 1000
        start_s = end_s - self.limit * 60 - 180
        start_iso = datetime.fromtimestamp(start_s, timezone.utc).isoformat().replace("+00:00", "Z")
        end_iso = datetime.fromtimestamp(end_s, timezone.utc).isoformat().replace("+00:00", "Z")
        raw = self._get_json(
            f"{self.coinbase_base}/products/{product}/candles",
            params={"granularity": 60, "start": start_iso, "end": end_iso},
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
                typical = (open_ + high + low + close) / 4.0
                candle = Candle(
                    open_ms=open_ms,
                    close_ms=open_ms + 60_000 - 1,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    quote_volume=max(0.0, base_volume * typical),
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
        book_tickers = self._binance_book_tickers() if binance else {}
        day_tickers = self._binance_24h_tickers() if binance else {}

        def choices(display: str) -> list[tuple[str, str]]:
            bases = alias_map.get(display.upper()) or (display.upper(),)
            result: list[tuple[str, str]] = []
            for provider in self.provider_order:
                source = binance if provider == "binance" else coinbase
                for base in bases:
                    symbol = source.get(base)
                    # Never fan out guessed symbols after a failed discovery call;
                    # that can exhaust the bounded request budget before the fallback provider.
                    if symbol and (provider, symbol) not in result:
                        result.append((provider, symbol))
            return result

        def load(display: str) -> IntradayMetrics:
            errors: list[str] = []
            for provider, symbol in choices(display):
                try:
                    candles = self._fetch_binance(symbol, now_ms) if provider == "binance" else self._fetch_coinbase(symbol, now_ms)
                    metrics = analyze_candles(
                        display,
                        provider,
                        symbol,
                        candles,
                        now_ms=now_ms,
                        ticker24=day_tickers.get(symbol),
                        book=book_tickers.get(symbol),
                    )
                    if metrics.candle_count >= 45:
                        return metrics
                    errors.append(f"{provider}:{symbol} only {metrics.candle_count} candles")
                except Exception as exc:
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
            "execution_count": sum(item.execution_data_available for item in result.values()),
            "requested_coins": len(unique),
            "diagnostics": list(self.diagnostics),
        }

    def enrich_top_candidates(
        self,
        metrics_by_display: Mapping[str, IntradayMetrics],
        candidates: Sequence[str],
        *,
        max_count: int = 12,
    ) -> dict[str, Any]:
        """Fill Coinbase spread snapshots only for the highest raw candidates."""
        checked = 0
        failures: list[str] = []
        for display in list(dict.fromkeys(str(value).upper() for value in candidates))[:max_count]:
            metrics = metrics_by_display.get(display)
            if metrics is None or metrics.provider != "coinbase" or not metrics.symbol:
                continue
            try:
                raw = self._get_json(f"{self.coinbase_base}/products/{metrics.symbol}/ticker")
                if not isinstance(raw, Mapping):
                    continue
                bid = _safe_float(raw.get("bid"))
                ask = _safe_float(raw.get("ask"))
                price = _safe_float(raw.get("price"))
                base_volume = _safe_float(raw.get("volume"))
                _apply_execution(metrics, bid=bid, ask=ask, bid_qty=None, ask_qty=None)
                if price and base_volume:
                    metrics.quote_volume_24h = max(metrics.quote_volume_24h, price * base_volume)
                checked += 1
            except Exception as exc:
                failures.append(f"{display}: {exc}")
        return {"checked": checked, "failures": failures, "requests": self.request_count}


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _pct(current: float, previous: float) -> float | None:
    return None if previous <= 0 else (current / previous - 1.0) * 100.0


def _median(values: Sequence[float]) -> float | None:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.median(cleaned) if cleaned else None


def _robust_z(value: float, samples: Sequence[float]) -> float:
    cleaned = [float(item) for item in samples if math.isfinite(float(item))]
    if len(cleaned) < 4:
        return 0.0
    centre = statistics.median(cleaned)
    deviations = [abs(item - centre) for item in cleaned]
    mad = statistics.median(deviations)
    scale = max(1e-12, mad * 1.4826, abs(centre) * 0.08)
    return (value - centre) / scale


def _window_price(candles: Sequence[Candle], minutes: int) -> float | None:
    if len(candles) <= minutes:
        return None
    return _pct(candles[-1].close, candles[-1 - minutes].close)


def _window_volume_context(candles: Sequence[Candle], minutes: int) -> tuple[float | None, float | None, float | None]:
    if len(candles) < minutes * 3:
        return None, None, None
    recent = sum(item.quote_volume for item in candles[-minutes:])
    previous = sum(item.quote_volume for item in candles[-2 * minutes : -minutes])
    samples: list[float] = []
    end = len(candles) - minutes
    while end - minutes >= 0 and len(samples) < 12:
        samples.append(sum(item.quote_volume for item in candles[end - minutes : end]))
        end -= minutes
    baseline = _median(samples)
    ratio = recent / baseline if baseline and baseline > 0 else None
    acceleration = recent / previous if previous > 0 else None
    z = _robust_z(recent, samples) if samples else None
    return ratio, acceleration, z


def _taker_share(candles: Sequence[Candle], minutes: int) -> float | None:
    selected = candles[-minutes:]
    if len(selected) < max(3, minutes // 2) or any(item.taker_buy_quote_volume is None for item in selected):
        return None
    total = sum(item.quote_volume for item in selected)
    buy = sum(float(item.taker_buy_quote_volume or 0.0) for item in selected)
    return buy / total if total > 0 else None


def _volume_color(ratio: float | None, z: float | None) -> str:
    if ratio is None:
        return WHITE
    z = float(z or 0.0)
    if ratio >= 2.7 and z >= 2.0:
        return PURPLE
    if ratio >= 1.55 and z >= 0.7:
        return GREEN
    if ratio >= 1.12:
        return BLUE
    if ratio <= 0.42 and z <= -1.1:
        return RED
    if ratio <= 0.78:
        return ORANGE
    return YELLOW


def _range_position(candles: Sequence[Candle], minutes: int) -> tuple[float | None, float | None, float | None]:
    selected = candles[-minutes:]
    if len(selected) < max(12, minutes // 2):
        return None, None, None
    low = min(item.low for item in selected)
    high = max(item.high for item in selected)
    close = selected[-1].close
    if high <= low:
        return 0.5, low, high
    return _clamp((close - low) / (high - low)), low, high


def _new_low_age_minutes(candles: Sequence[Candle], minutes: int = 180) -> float | None:
    selected = candles[-minutes:]
    if len(selected) < max(30, minutes // 2):
        return None
    index = min(range(len(selected)), key=lambda position: selected[position].low)
    return float(len(selected) - 1 - index)


def _discount_recovery_context(
    candles: Sequence[Candle],
    minutes: int = 180,
) -> tuple[float, float, float | None]:
    """Return pre-low drawdown, rebound from the low and low age in minutes.

    Only information available at the current candle is used.  The prior high is
    restricted to candles at or before the local low, preventing the later
    rebound from inflating the measured discount.
    """
    selected = list(candles[-minutes:])
    if len(selected) < max(30, minutes // 2):
        return 0.0, 0.0, None
    low_index = min(range(len(selected)), key=lambda position: selected[position].low)
    low = float(selected[low_index].low)
    close = float(selected[-1].close)
    prior_high = max(float(item.high) for item in selected[: low_index + 1])
    drawdown = max(0.0, (prior_high / low - 1.0) * 100.0) if low > 0 else 0.0
    rebound = max(0.0, (close / low - 1.0) * 100.0) if low > 0 else 0.0
    age = float(len(selected) - 1 - low_index)
    return drawdown, rebound, age


def _log_ratio_score(ratio: float | None) -> float:
    if ratio is None or ratio <= 0:
        return 0.0
    return 100.0 * _clamp((math.log(ratio, 2.0) + 1.0) / 3.0)


def _apply_execution(
    metrics: IntradayMetrics,
    *,
    bid: float | None,
    ask: float | None,
    bid_qty: float | None,
    ask_qty: float | None,
) -> None:
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return
    midpoint = (bid + ask) / 2.0
    spread = (ask - bid) / midpoint * 100.0 if midpoint > 0 else None
    depth = None
    if bid_qty is not None and ask_qty is not None:
        depth = min(max(0.0, bid_qty * bid), max(0.0, ask_qty * ask))
    spread_score = 100.0 * (1.0 - _clamp(((spread or 1.2) - 0.05) / 0.95))
    depth_score = 50.0 if depth is None else 100.0 * _clamp(math.log10(max(depth, 1.0)) / 6.0)
    metrics.bid = bid
    metrics.ask = ask
    metrics.spread_pct = spread
    metrics.top_book_depth_usd = depth
    metrics.execution_quality_score = round(0.76 * spread_score + 0.24 * depth_score, 4)
    metrics.execution_data_available = True


def analyze_candles(
    display: str,
    provider: str,
    symbol: str,
    candles: Iterable[Candle],
    *,
    now_ms: int,
    ticker24: Mapping[str, Any] | None = None,
    book: Mapping[str, Any] | None = None,
) -> IntradayMetrics:
    ordered = sorted({item.open_ms: item for item in candles}.values(), key=lambda item: item.open_ms)
    if not ordered:
        return IntradayMetrics(display=display, provider=provider, symbol=symbol)
    coverage_hours = max(0.0, (ordered[-1].close_ms - ordered[0].open_ms) / 3_600_000.0)
    quality = "good" if len(ordered) >= 240 and coverage_hours >= 3.8 else (
        "partial" if len(ordered) >= 75 and coverage_hours >= 1.1 else "insufficient"
    )

    price_changes = {minutes: _window_price(ordered, minutes) for minutes in (1, 3, 5, 10, 15, 30, 60, 180)}
    volume_ratios: dict[int, float | None] = {}
    volume_z: dict[int, float | None] = {}
    volume_acceleration: dict[int, float | None] = {}
    volume_colors: dict[int, str] = {}
    for minutes in (3, 5, 10, 15, 30, 60):
        ratio, acceleration, z = _window_volume_context(ordered, minutes)
        volume_ratios[minutes] = ratio
        volume_acceleration[minutes] = acceleration
        volume_z[minutes] = z
        volume_colors[minutes] = _volume_color(ratio, z)

    taker = {minutes: _taker_share(ordered, minutes) for minutes in (5, 15, 30, 60)}
    pos180, low180, _ = _range_position(ordered, 180)
    pos300, low300, high300 = _range_position(ordered, min(300, len(ordered)))
    close = ordered[-1].close
    recent_drawdown, rebound_from_low, age_low = _discount_recovery_context(ordered, 180)

    high24 = _safe_float((ticker24 or {}).get("highPrice"))
    low24 = _safe_float((ticker24 or {}).get("lowPrice"))
    day_change = _safe_float((ticker24 or {}).get("priceChangePercent"))
    quote24 = _safe_float((ticker24 or {}).get("quoteVolume")) or 0.0
    if high24 and low24 and high24 > low24:
        pos1440 = _clamp((close - low24) / (high24 - low24))
        distance_high = _pct(high24, close)
    else:
        pos1440 = pos300
        distance_high = _pct(float(high300), close) if high300 else None
    distance_low = _pct(close, float(low180)) if low180 else None

    ratio_score = (
        0.20 * _log_ratio_score(volume_ratios.get(5))
        + 0.34 * _log_ratio_score(volume_ratios.get(15))
        + 0.30 * _log_ratio_score(volume_ratios.get(30))
        + 0.16 * _log_ratio_score(volume_ratios.get(60))
    )
    acceleration_values = [
        float(value)
        for value in (volume_acceleration.get(5), volume_acceleration.get(15), volume_acceleration.get(30))
        if value is not None and math.isfinite(float(value))
    ]
    acceleration_score = (
        statistics.mean(100.0 * _clamp((math.log(max(value, 1e-6), 2.0) + 1.0) / 3.0) for value in acceleration_values)
        if acceleration_values else 50.0
    )
    buy_values = [float(value) for value in taker.values() if value is not None]
    buy_score = statistics.mean(100.0 * _clamp((value - 0.38) / 0.24) for value in buy_values) if buy_values else 50.0
    demand = 0.58 * ratio_score + 0.23 * acceleration_score + 0.19 * buy_score

    p5 = float(price_changes.get(5) or 0.0)
    p15 = float(price_changes.get(15) or 0.0)
    p30 = float(price_changes.get(30) or 0.0)
    p60 = float(price_changes.get(60) or 0.0)
    p180 = float(price_changes.get(180) or 0.0)
    p24 = float(day_change or 0.0)
    negative_votes = sum((p5 < -0.12, p15 < -0.30, p30 < -0.55, p60 < -1.0, p180 < -2.5))
    fresh_low = age_low is not None and age_low <= 12.0
    falling_knife = bool(
        p15 <= -0.65
        or p30 <= -1.05
        or p60 <= -1.9
        or p180 <= -4.0
        or (fresh_low and p5 < -0.04)
        or negative_votes >= 4
    )

    ideal_low_position = 1.0 - min(1.0, abs(float(pos180 or 0.5) - 0.34) / 0.46)
    slope_stability = 1.0 - _clamp(abs(p180 - 0.35) / 4.5)
    short_stability = 1.0 - _clamp(max(abs(p5) * 1.8, abs(p15), abs(p30) * 0.65) / 1.5)
    low_age_score = 0.45 if age_low is None else _clamp((age_low - 8.0) / 90.0)
    base_quality = 100.0 * (0.33 * ideal_low_position + 0.27 * slope_stability + 0.24 * short_stability + 0.16 * low_age_score)
    if falling_knife:
        base_quality *= 0.12

    # A buy setup must be discounted, not merely active.  The discount score
    # combines a real preceding pullback with a low range position and distance
    # from the 24h high.  Stabilization is deliberately separate: a fresh low
    # remains unsafe until the short windows hold and a modest rebound survives.
    range_discount = 1.0 - _clamp((float(pos180 or 0.50) - 0.18) / 0.52)
    drawdown_discount = _clamp((recent_drawdown - 0.45) / 3.80)
    day_discount = 1.0 - float(pos1440 if pos1440 is not None else pos300 if pos300 is not None else 0.50)
    high_discount = 0.50 if distance_high is None else _clamp((float(distance_high) - 1.2) / 7.0)
    cheap_price = 100.0 * (
        0.36 * range_discount
        + 0.31 * drawdown_discount
        + 0.19 * day_discount
        + 0.14 * high_discount
    )

    age_score = 0.0 if age_low is None else _clamp((float(age_low) - 4.0) / 32.0)
    if age_low is not None and age_low > 150.0:
        age_score *= _clamp((210.0 - float(age_low)) / 60.0)
    hold5 = _clamp((p5 + 0.08) / 0.30)
    hold15 = _clamp((p15 + 0.20) / 0.62)
    hold30 = _clamp((p30 + 0.42) / 1.05)
    short_hold = 0.42 * hold5 + 0.34 * hold15 + 0.24 * hold30
    rebound_score = _clamp((rebound_from_low - 0.08) / 0.75)
    if rebound_from_low > 3.5:
        rebound_score *= _clamp((5.5 - rebound_from_low) / 2.0)
    low_zone_hold = 1.0 - _clamp((float(pos180 or 0.50) - 0.52) / 0.30)
    stabilization = 100.0 * (0.31 * age_score + 0.37 * short_hold + 0.20 * rebound_score + 0.12 * low_zone_hold)
    if falling_knife:
        stabilization *= 0.10
    elif age_low is not None and age_low < 4.0:
        stabilization *= 0.25
    if rebound_from_low > 4.5 or float(pos180 or 0.50) > 0.72:
        stabilization *= 0.62

    # Balanced entry gate: a real modest discount plus a beginning hold is
    # sufficient for an early blue signal.  The stricter legacy-quality gate
    # remains separate and is required before green or purple is possible.
    discount_qualified = bool(
        cheap_price >= 48.0
        and recent_drawdown >= 0.32
        and float(pos180 or 1.0) <= 0.68
    )
    stabilized_after_drop = bool(
        discount_qualified
        and stabilization >= 48.0
        and age_low is not None
        and 4.0 <= float(age_low) <= 180.0
        and p5 >= -0.12
        and p15 >= -0.28
        and p30 >= -0.55
        and rebound_from_low <= 4.2
        and not falling_knife
    )
    confirmed_recovery = bool(
        cheap_price >= 58.0
        and recent_drawdown >= 0.65
        and float(pos180 or 1.0) <= 0.60
        and stabilization >= 60.0
        and age_low is not None
        and 8.0 <= float(age_low) <= 150.0
        and p5 >= -0.08
        and p15 >= -0.20
        and p30 >= -0.42
        and 0.08 <= rebound_from_low <= 3.8
        and not falling_knife
    )

    room_from_high = 50.0 if distance_high is None else 100.0 * _clamp((distance_high - 0.7) / 5.4)
    range_room = 50.0 if pos1440 is None else 100.0 * _clamp((0.92 - pos1440) / 0.70)
    room = 0.62 * room_from_high + 0.38 * range_room

    overextension = 0.0
    if p30 > 2.0:
        overextension += _clamp((p30 - 2.0) / 3.5) * 24.0
    if p60 > 3.4:
        overextension += _clamp((p60 - 3.4) / 5.0) * 30.0
    if p180 > 6.0:
        overextension += _clamp((p180 - 6.0) / 8.0) * 28.0
    if p24 > 10.0:
        overextension += _clamp((p24 - 10.0) / 15.0) * 18.0
    if pos180 is not None and pos180 > 0.88:
        overextension += _clamp((pos180 - 0.88) / 0.12) * 20.0
    decelerating = sum(
        value is not None and float(value) < 0.82
        for value in (volume_acceleration.get(5), volume_acceleration.get(15), volume_acceleration.get(30))
    ) >= 2
    late_entry = bool(overextension >= 38.0 or ((p60 > 2.4 or p180 > 4.8) and decelerating))
    overextension = min(100.0, overextension + (16.0 if decelerating and p60 > 1.4 else 0.0))

    ratio30 = float(volume_ratios.get(30) or 1.0)
    buy30 = taker.get(30)
    active_sell = _clamp((-p30) / 1.6) * _clamp((ratio30 - 0.80) / 1.7)
    unsupported_rise = _clamp(p30 / 1.8) * _clamp((0.95 - ratio30) / 0.65)
    seller_share = 0.0 if buy30 is None else _clamp((0.50 - float(buy30)) / 0.15)
    base_break = 1.0 if falling_knife else _clamp((-p60 - 0.30) / 1.9)
    sell_pressure = 100.0 * _clamp(0.40 * active_sell + 0.25 * unsupported_rise + 0.16 * seller_share + 0.19 * base_break)

    if falling_knife:
        demand *= 0.10
    elif p30 < -0.25:
        demand *= _clamp((p30 + 0.95) / 0.70)

    metrics = IntradayMetrics(
        display=display,
        provider=provider,
        symbol=symbol,
        interval_minutes=1,
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
        cheap_price_score=round(_clamp(cheap_price / 100.0) * 100.0, 4),
        stabilization_score=round(_clamp(stabilization / 100.0) * 100.0, 4),
        recent_drawdown_pct=round(recent_drawdown, 6),
        rebound_from_low_pct=round(rebound_from_low, 6),
        discount_qualified=discount_qualified,
        stabilized_after_drop=stabilized_after_drop,
        confirmed_recovery=confirmed_recovery,
        room_to_target_score=round(_clamp(room / 100.0) * 100.0, 4),
        overextension_penalty=round(_clamp(overextension / 100.0) * 100.0, 4),
        falling_knife=falling_knife,
        late_entry=late_entry,
        range_position_180=None if pos180 is None else round(pos180, 6),
        range_position_300=None if pos300 is None else round(pos300, 6),
        range_position_1440=None if pos1440 is None else round(pos1440, 6),
        distance_to_24h_high_pct=None if distance_high is None else round(distance_high, 6),
        distance_above_3h_low_pct=None if distance_low is None else round(distance_low, 6),
        new_3h_low_age_minutes=None if age_low is None else round(age_low, 3),
        quote_volume_24h=round(quote24 if quote24 > 0 else sum(item.quote_volume for item in ordered), 4),
        high_24h=high24,
        low_24h=low24,
        price_change_24h_pct=day_change,
        latest_candle_open_ms=int(ordered[-1].open_ms),
        latest_high=round(float(ordered[-1].high), 12),
        latest_low=round(float(ordered[-1].low), 12),
        latest_close=round(float(ordered[-1].close), 12),
    )
    if book:
        _apply_execution(
            metrics,
            bid=_safe_float(book.get("bidPrice")),
            ask=_safe_float(book.get("askPrice")),
            bid_qty=_safe_float(book.get("bidQty")),
            ask_qty=_safe_float(book.get("askQty")),
        )

    reasons: list[str] = []
    if demand >= 70:
        reasons.append("1m-Nachfrage beschleunigt")
    if base_quality >= 70:
        reasons.append("stabile 3h-Basis")
    if discount_qualified:
        reasons.append("günstige Rücklaufzone")
    if stabilized_after_drop:
        reasons.append("Stabilisierung beginnt")
    if confirmed_recovery:
        reasons.append("Erholung vollständig bestätigt")
    if falling_knife:
        reasons.append("Falling-Knife-Sperre")
    if late_entry:
        reasons.append("bereits weit gelaufen")
    if taker.get(30) is not None and float(taker[30]) >= 0.58:
        reasons.append("Taker-Kaufanteil hoch")
    if metrics.spread_pct is not None and metrics.spread_pct > 0.60:
        reasons.append("Spread erhöht")
    metrics.reasons = tuple(reasons)
    return metrics
# Package revision: v3.5.0-balanced-entry-r4
