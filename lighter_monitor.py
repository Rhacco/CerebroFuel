"""Lighter-native market discovery, execution filter and 10/20/60m signals."""
from __future__ import annotations

import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

APP_VERSION = "3.6.0"
WINDOWS = (10, 20, 60)
COLORS = {
    "BUY": "🟣", "WATCH_LONG": "🟢", "NO_TRADE": "🟡",
    "WATCH_SHORT": "🟠", "SELL": "🔴", "INVALID_DATA": "🟤",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass
class Window:
    minutes: int
    price_pct: float | None = None
    volume_ratio: float | None = None
    score: float = 0.0
    quality: str = "invalid"
    reason: str = "missing candles"


@dataclass
class Signal:
    symbol: str
    alias: str
    state: str = "INVALID_DATA"
    opportunity: float = 0.0
    direction: float = 0.0
    confidence: float = 0.0
    cost_pct: float | None = None
    funding_hourly_pct: float | None = None
    volume_24h: float = 0.0
    open_interest_usd: float = 0.0
    volume_oi: float | None = None
    windows: dict[int, Window] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


class LighterClient:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, **params: Any) -> Mapping[str, Any]:
        url = self.base + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "cf/3.6.0"})
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        if not isinstance(payload, Mapping) or int(payload.get("code", 200)) != 200:
            raise RuntimeError(f"Lighter-Antwort ungültig: {path}")
        return payload

    def markets(self) -> list[Mapping[str, Any]]:
        return list(self.get("/orderBookDetails").get("order_book_details") or [])

    def funding(self) -> list[Mapping[str, Any]]:
        return list(self.get("/funding-rates").get("funding_rates") or [])

    def candles(self, market_id: int, count: int = 180) -> list[Mapping[str, Any]]:
        now = int(time.time())
        payload = self.get(
            "/candles", market_id=market_id, resolution="1m",
            start_timestamp=now - (count + 10) * 60, end_timestamp=now,
            count_back=count, set_timestamp_to_end="true",
        )
        rows = list(payload.get("c") or [])
        current_open = (now // 60) * 60_000
        return [row for row in rows if int(row.get("t", 0)) < current_open]

    def book(self, market_id: int, limit: int = 50) -> Mapping[str, Any]:
        return self.get("/orderBookOrders", market_id=market_id, limit=limit)


def _window(candles: list[Mapping[str, Any]], minutes: int) -> Window:
    if len(candles) < minutes * 2:
        return Window(minutes=minutes, reason=f"{len(candles)}/{minutes * 2} candles")
    recent, previous = candles[-minutes:], candles[-2 * minutes:-minutes]
    try:
        start, end = _f(recent[0]["o"]), _f(recent[-1]["c"])
        recent_volume = sum(_f(row.get("V")) for row in recent)
        previous_volume = sum(_f(row.get("V")) for row in previous)
    except (KeyError, TypeError):
        return Window(minutes=minutes, reason="malformed candle")
    expected = 60_000
    gaps = [
        int(b.get("t", 0)) - int(a.get("t", 0))
        for a, b in zip(recent, recent[1:])
    ]
    if start <= 0 or previous_volume <= 0 or any(gap != expected for gap in gaps):
        return Window(minutes=minutes, reason="price/volume/gap")
    price = (end / start - 1.0) * 100.0
    ratio = recent_volume / previous_volume
    # Positive accumulation: stable/slightly rising price + rising volume.
    # Negative distribution: falling price + rising volume.
    volume_impulse = _clamp((ratio - 1.0) * 70.0, -45.0, 55.0)
    if price >= -0.08:
        score = _clamp(price * 30.0 + volume_impulse, -100.0, 100.0)
    else:
        score = _clamp(price * 36.0 - max(0.0, volume_impulse), -100.0, 100.0)
    return Window(minutes, price, ratio, score, "ok", "")


def _levels(book: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    rows = book.get(side) or []
    result = []
    for row in rows:
        price = _f(row.get("price"))
        size = _f(row.get("remaining_base_amount", row.get("size")))
        if price > 0 and size > 0:
            result.append((price, size))
    return result


def _vwap(levels: list[tuple[float, float]], quote: float) -> float | None:
    spent = base = 0.0
    for price, size in levels:
        take_quote = min(quote - spent, price * size)
        spent += take_quote
        base += take_quote / price
        if spent >= quote - 1e-9:
            return spent / base
    return None


def _roundtrip_cost(book: Mapping[str, Any], quote: float) -> float | None:
    asks, bids = _levels(book, "asks"), _levels(book, "bids")
    buy, sell = _vwap(asks, quote), _vwap(bids, quote)
    if buy is None or sell is None or buy <= 0:
        return None
    return max(0.0, (buy - sell) / buy * 100.0)


class LighterMonitor:
    def __init__(self, config: Mapping[str, Any], client: LighterClient | None = None) -> None:
        self.config = config
        self.client = client or LighterClient(
            str(config.get("lighter_base_url", "https://mainnet.zklighter.elliot.ai/api/v1")),
            float(config.get("request_timeout_seconds", 15)),
        )

    def _analyse(
        self, market: Mapping[str, Any], funding: float | None,
        candles: list[Mapping[str, Any]], book: Mapping[str, Any],
    ) -> Signal:
        symbol = str(market["symbol"]).upper()
        aliases = self.config.get("aliases") or {}
        signal = Signal(symbol=symbol, alias=str(aliases.get(symbol, symbol[:3])).upper())
        signal.volume_24h = _f(market.get("daily_quote_token_volume"))
        mark = _f(market.get("mark_price"))
        signal.open_interest_usd = _f(market.get("open_interest")) * mark
        signal.volume_oi = (
            signal.volume_24h / signal.open_interest_usd if signal.open_interest_usd > 0 else None
        )
        signal.funding_hourly_pct = None if funding is None else funding * 100.0
        signal.cost_pct = _roundtrip_cost(book, float(self.config.get("execution_quote_usdc", 50)))
        signal.windows = {minutes: _window(candles, minutes) for minutes in WINDOWS}
        good = [item for item in signal.windows.values() if item.quality == "ok"]
        if len(good) < 2:
            signal.reasons.append("zu wenige gültige Fenster")
            return signal

        liquidity = _clamp(20.0 * math.log10(max(signal.volume_24h, 1.0) / 100_000.0))
        oi = _clamp(18.0 * math.log10(max(signal.open_interest_usd, 1.0) / 100_000.0))
        cost_score = 0.0 if signal.cost_pct is None else _clamp(100.0 - signal.cost_pct * 650.0)
        movement = statistics.mean(
            min(100.0, abs(item.price_pct or 0.0) * 65.0 + abs((item.volume_ratio or 1) - 1) * 35.0)
            for item in good
        )
        signal.opportunity = _clamp(liquidity * .35 + oi * .20 + cost_score * .25 + movement * .20)

        weights = {10: .30, 20: .45, 60: .25}
        weighted = sum(item.score * weights[item.minutes] for item in good) / sum(
            weights[item.minutes] for item in good
        )
        positive = sum(item.score >= 12 for item in good)
        negative = sum(item.score <= -12 for item in good)
        agreement = max(positive, negative) / len(good)
        signal.direction = _clamp(weighted, -100.0, 100.0)
        signal.confidence = _clamp(signal.opportunity * .55 + abs(weighted) * .30 + agreement * 15)

        funding_limit = float(self.config.get("max_abs_funding_hourly_pct", 0.005))
        funding_bad_long = signal.funding_hourly_pct is not None and signal.funding_hourly_pct > funding_limit
        funding_bad_short = signal.funding_hourly_pct is not None and signal.funding_hourly_pct < -funding_limit
        executable = signal.cost_pct is not None and signal.cost_pct <= float(
            self.config.get("max_roundtrip_cost_pct", 0.15)
        )
        enough_volume = signal.volume_24h >= float(self.config.get("minimum_volume_24h_usdc", 500_000))

        if not executable or not enough_volume:
            signal.state = "NO_TRADE"
            signal.reasons.append("Kosten/Liquidität")
        elif weighted >= 38 and positive >= 2 and not funding_bad_long:
            signal.state = "BUY"
            signal.reasons.append("Akkumulation 2/3")
        elif weighted >= 15 and positive >= 2 and not funding_bad_long:
            signal.state = "WATCH_LONG"
            signal.reasons.append("Long-Aufbau")
        elif weighted <= -38 and negative >= 2 and not funding_bad_short:
            signal.state = "SELL"
            signal.reasons.append("Verkaufsdruck 2/3")
        elif weighted <= -15 and negative >= 2 and not funding_bad_short:
            signal.state = "WATCH_SHORT"
            signal.reasons.append("Short-Aufbau")
        else:
            signal.state = "NO_TRADE"
            signal.reasons.append("keine Mehrfenster-Freigabe")
        if funding_bad_long or funding_bad_short:
            signal.reasons.append("Fundingfilter")
        return signal

    def _format(self, signals: list[Signal], now: datetime) -> str:
        btc = next((item for item in signals if item.symbol == "BTC"), None)
        valid = [item for item in signals if item.state != "INVALID_DATA"]
        market_color = "🟢" if btc and btc.direction >= 8 else ("🔴" if btc and btc.direction <= -8 else "🟡")
        quality_color = "🟢" if len(valid) >= 10 else ("🟡" if len(valid) >= 7 else "🟤")
        header = (
            f"BTC{market_color}MKT{quality_color}LQ{quality_color}"
            f"FND{quality_color}QLT{quality_color}"
            f"{now.astimezone(ZoneInfo(str(self.config.get('timezone', 'Europe/Berlin')))).strftime(':%M')}"
        )
        ranked = sorted(
            signals,
            key=lambda item: (
                item.symbol != "BTC",
                item.state in {"BUY", "SELL", "WATCH_LONG", "WATCH_SHORT"},
                item.confidence,
                item.opportunity,
            ),
            reverse=True,
        )
        top = ranked[: int(self.config.get("top_coin_count", 8))]
        lines = [header]
        for item in top:
            color = COLORS[item.state]
            arrow = "▲" if item.direction > 8 else ("▼" if item.direction < -8 else "·")
            funding = "?" if item.funding_hourly_pct is None else (
                "+" if item.funding_hourly_pct > .00005 else ("-" if item.funding_hourly_pct < -.00005 else "0")
            )
            window_colors = "".join(
                "🟤" if window.quality != "ok" else (
                    "🟢" if window.score >= 12 else ("🔴" if window.score <= -12 else "🟡")
                )
                for window in item.windows.values()
            )
            line = (
                f"{color}{int(round(item.confidence)):02d}{arrow}"
                f"O{int(round(item.opportunity)):02d}"
                f"D{int(round(abs(item.direction))):02d}"
                f"C{int(round(item.cost_pct * 100)) if item.cost_pct is not None else 99:02d}"
                f"F{funding}V{window_colors}{item.alias}"
            )
            lines.append(line[: int(self.config.get("discord_max_codepoints_per_line", 34))])
        return "\n".join(lines)

    def run(self) -> tuple[str, dict[str, Any]]:
        now = datetime.now(timezone.utc)
        allowed = {str(x).upper() for x in self.config.get("candidate_symbols", [])}
        markets = {
            str(row.get("symbol")).upper(): row
            for row in self.client.markets()
            if str(row.get("symbol")).upper() in allowed
            and str(row.get("status")).lower() == "active"
            and str(row.get("market_type")).lower() == "perp"
        }
        raw_funding = self.client.funding()
        funding: dict[str, float] = {}
        for row in raw_funding:
            symbol = str(row.get("symbol")).upper()
            if symbol not in allowed:
                continue
            rate = _f(row.get("rate"))
            if str(row.get("exchange")).lower() == "lighter" or symbol not in funding:
                funding[symbol] = rate

        signals: list[Signal] = []
        workers = min(8, max(1, int(self.config.get("parallel_requests", 6))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._load_one, row): symbol for symbol, row in markets.items()
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    candles, book = future.result()
                    signals.append(self._analyse(markets[symbol], funding.get(symbol), candles, book))
                except Exception as exc:
                    aliases = self.config.get("aliases") or {}
                    signals.append(Signal(
                        symbol=symbol, alias=str(aliases.get(symbol, symbol[:3])),
                        reasons=[f"{type(exc).__name__}: {exc}"],
                    ))
        for symbol in sorted(allowed - set(markets)):
            signals.append(Signal(symbol, str((self.config.get("aliases") or {}).get(symbol, symbol[:3])),
                                  reasons=["kein aktiver Lighter-Krypto-Perp"]))
        report = self._format(signals, now)
        max_len = int(self.config.get("discord_max_codepoints_per_line", 34))
        if len(report.splitlines()) != int(self.config.get("top_coin_count", 8)) + 1:
            raise RuntimeError("Discord-Ausgabe hat nicht neun Zeilen")
        if any(len(line) > max_len for line in report.splitlines()):
            raise RuntimeError("Discord-Zeilenlimit überschritten")
        payload = {
            "version": APP_VERSION, "generated_at": now.isoformat(),
            "report": report, "signals": [asdict(item) for item in signals],
        }
        return report, payload

    def _load_one(self, market: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
        market_id = int(market["market_id"])
        return self.client.candles(market_id), self.client.book(market_id)

# Package revision: v3.6.0-lighter-structure-r2
