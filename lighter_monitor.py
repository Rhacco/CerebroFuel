"""Lighter-native P/T/W monitor for early, confirmed manual entries."""
from __future__ import annotations

import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

APP_VERSION = "3.6.3"
PACKAGE_REVISION = "v3.6.3-ptw-precision-r3"
ANALYSIS_WINDOWS = (5, 10, 15, 20, 60)
DISPLAY_WINDOWS = (5, 20, 60)
PRESSURE_WINDOWS = (10, 20, 60)
TREND_WINDOWS = (5, 15, 60)
# Kept as the stable public display contract.
WINDOWS = DISPLAY_WINDOWS

SUMMARY_COLORS = {
    "BUY": "🟢",
    "SELL": "🔴",
    "STRONG_LONG": "🟢",
    "STRONG_SHORT": "🔴",
    "WATCH_LONG": "🔵",
    "WATCH_SHORT": "🟠",
    "NO_TRADE": "🟡",
    "INVALID_DATA": "⚫",
}
STATE_TIER = {
    "BUY": 4,
    "SELL": 4,
    "STRONG_LONG": 3,
    "STRONG_SHORT": 3,
    "WATCH_LONG": 2,
    "WATCH_SHORT": 2,
    "NO_TRADE": 1,
    "INVALID_DATA": 0,
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    value = int(_f(row.get("t")))
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _pct(start: float, end: float) -> float:
    return 0.0 if start <= 0 else (end / start - 1.0) * 100.0


def _mean(values: Iterable[float], default: float = 0.0) -> float:
    rows = list(values)
    return statistics.mean(rows) if rows else default


def _direction(value: float, fallback: int = 1) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 1 if fallback >= 0 else -1


def _directional(value: float, direction: int) -> float:
    return value * (1 if direction >= 0 else -1)


@dataclass
class Window:
    minutes: int
    price_pct: float | None = None
    volume_ratio: float | None = None
    score: float = 0.0
    quality: str = "invalid"
    reason: str = "missing candles"


@dataclass
class Setup:
    kind: str
    direction: int = 0
    score: float = 0.0
    phase: str = "none"
    volume_score: float = 0.0
    age_minutes: int | None = None
    exit_hint: bool = False
    confirmations: int = 0
    event_strength: float = 0.0
    event_timestamp_ms: int | None = None
    move_pct: float = 0.0
    rejection_fraction: float = 0.0
    peak_score: float = 0.0
    reason: str = ""


@dataclass
class Signal:
    symbol: str
    alias: str
    state: str = "INVALID_DATA"
    selected_setup: str = "NONE"
    opportunity: float = 0.0
    trade_readiness: float = 0.0
    direction: float = 0.0
    confidence: float = 0.0
    data_quality: float = 0.0
    activity_score: float = 0.0
    execution_score: float = 0.0
    liquidity_score: float = 0.0
    volume_confirmation: float = 0.0
    btc_context: float | None = None
    cost_pct: float | None = None
    funding_hourly_pct: float | None = None
    volume_24h: float = 0.0
    open_interest_usd: float = 0.0
    volume_oi: float | None = None
    windows: dict[int, Window] = field(default_factory=dict)
    pressure: Setup = field(default_factory=lambda: Setup("PRESSURE"))
    trend: Setup = field(default_factory=lambda: Setup("TREND"))
    reversal: Setup = field(default_factory=lambda: Setup("REVERSAL"))
    reasons: list[str] = field(default_factory=list)


class LighterClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 15.0,
        retries: int = 3,
        closed_candle_delay_seconds: int = 8,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, min(4, retries))
        self.closed_candle_delay_seconds = max(0, min(30, closed_candle_delay_seconds))

    def get(self, path: str, **params: Any) -> Mapping[str, Any]:
        url = self.base + path
        if params:
            url += "?" + urlencode(params)
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": f"cf/{APP_VERSION}"},
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, Mapping) or int(payload.get("code", 200)) != 200:
                    raise RuntimeError(f"Lighter-Antwort ungültig: {path}")
                return payload
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise
            except (URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
            if attempt + 1 < self.retries:
                time.sleep(0.45 * (2 ** attempt))
        raise RuntimeError(f"Lighter-Aufruf fehlgeschlagen: {path}: {last_error}")

    def markets(self) -> list[Mapping[str, Any]]:
        return list(self.get("/orderBookDetails", filter="perp").get("order_book_details") or [])

    def funding(self) -> list[Mapping[str, Any]]:
        return list(self.get("/funding-rates").get("funding_rates") or [])

    def candles(self, market_id: int, count: int = 360) -> list[Mapping[str, Any]]:
        now = int(time.time())
        payload = self.get(
            "/candles",
            market_id=market_id,
            resolution="1m",
            start_timestamp=now - (count + 15) * 60,
            end_timestamp=now,
            count_back=min(500, max(130, count)),
            set_timestamp_to_end="true",
        )
        rows_by_time: dict[int, Mapping[str, Any]] = {}
        closed_end_ms = (
            (now - self.closed_candle_delay_seconds) // 60
        ) * 60_000
        for row in list(payload.get("c") or []):
            stamp = _timestamp_ms(row)
            if stamp > 0 and stamp <= closed_end_ms:
                rows_by_time[stamp] = row
        rows = [rows_by_time[key] for key in sorted(rows_by_time)]
        if rows and _timestamp_ms(rows[-1]) < closed_end_ms - 120_000:
            raise RuntimeError("Kerzendaten sind veraltet")
        return rows[-count:]

    def book(self, market_id: int, limit: int = 50) -> Mapping[str, Any]:
        return self.get("/orderBookOrders", market_id=market_id, limit=limit)


def _contiguous(rows: list[Mapping[str, Any]]) -> bool:
    return all(
        _timestamp_ms(current) - _timestamp_ms(previous) == 60_000
        for previous, current in zip(rows, rows[1:])
    )


def _window(candles: list[Mapping[str, Any]], minutes: int) -> Window:
    needed = minutes * 2
    if len(candles) < needed:
        return Window(minutes=minutes, reason=f"{len(candles)}/{needed} candles")
    rows = candles[-needed:]
    if not _contiguous(rows):
        return Window(minutes=minutes, reason="candle gap")
    recent, previous = rows[-minutes:], rows[:-minutes]
    try:
        prices = [
            _f(row[key])
            for row in rows
            for key in ("o", "h", "l", "c")
        ]
        start, end = _f(recent[0]["o"]), _f(recent[-1]["c"])
    except (KeyError, TypeError):
        return Window(minutes=minutes, reason="malformed candle")
    recent_volume = sum(_f(row.get("V")) for row in recent)
    previous_volume = sum(_f(row.get("V")) for row in previous)
    if min(prices, default=0.0) <= 0 or start <= 0 or min(recent_volume, previous_volume) <= 0:
        return Window(minutes=minutes, reason="price/quote-volume")

    price = _pct(start, end)
    ratio = recent_volume / previous_volume
    returns = [
        abs(_pct(_f(left.get("c")), _f(right.get("c"))))
        for left, right in zip(rows, rows[1:])
        if _f(left.get("c")) > 0 and _f(right.get("c")) > 0
    ]
    noise = max(0.015, statistics.median(returns) * 1.4826 if returns else 0.015)
    expected_move = max(0.06, noise * math.sqrt(minutes))
    price_units = _clamp(price / expected_move, -3.0, 3.0)
    volume_impulse = _clamp(math.log(max(ratio, 1e-9)) * 45.0, -45.0, 55.0)
    flat_tolerance = max(0.04, expected_move * 0.32)

    # Rising quote volume confirms stable/rising price as accumulation and
    # confirms falling price as selling pressure. Drying volume dampens a
    # counter-trend pullback instead of falsely treating it as a full reversal.
    if price >= -flat_tolerance:
        score = price_units * 25.0 + volume_impulse
    else:
        score = (
            price_units * 30.0
            - max(0.0, volume_impulse)
            + max(0.0, -volume_impulse) * 0.45
        )
    return Window(minutes, price, ratio, _clamp(score, -100.0, 100.0), "ok", "")


def _pressure_window(candles: list[Mapping[str, Any]], minutes: int) -> Window:
    """Measure directional price/quote-volume pressure."""
    needed = minutes * 2
    if len(candles) < needed:
        return Window(minutes=minutes, reason=f"{len(candles)}/{needed} candles")
    rows = candles[-needed:]
    if not _contiguous(rows):
        return Window(minutes=minutes, reason="candle gap")
    recent, previous = rows[-minutes:], rows[:-minutes]
    try:
        prices = [
            _f(row[key])
            for row in rows
            for key in ("o", "h", "l", "c")
        ]
        start, end = _f(recent[0]["o"]), _f(recent[-1]["c"])
    except (KeyError, TypeError):
        return Window(minutes=minutes, reason="malformed candle")
    recent_volume = sum(_f(row.get("V")) for row in recent)
    previous_volume = sum(_f(row.get("V")) for row in previous)
    if min(prices, default=0.0) <= 0 or start <= 0 or min(recent_volume, previous_volume) <= 0:
        return Window(minutes=minutes, reason="price/quote-volume")

    price = _pct(start, end)
    ratio = recent_volume / previous_volume
    volume_impulse = _clamp((ratio - 1.0) * 70.0, -45.0, 55.0)
    if price >= -0.08:
        score = price * 30.0 + volume_impulse
    else:
        score = price * 36.0 - max(0.0, volume_impulse)
    return Window(
        minutes,
        price,
        ratio,
        _clamp(score, -100.0, 100.0),
        "ok",
        "",
    )


def _pressure_once(candles: list[Mapping[str, Any]]) -> Setup:
    windows = {
        minutes: _pressure_window(candles, minutes)
        for minutes in PRESSURE_WINDOWS
    }
    good = [window for window in windows.values() if window.quality == "ok"]
    if len(good) < 2:
        return Setup("PRESSURE", reason="insufficient pressure windows")

    weights = {10: 0.30, 20: 0.45, 60: 0.25}
    usable_weight = sum(weights[window.minutes] for window in good)
    weighted = sum(
        window.score * weights[window.minutes]
        for window in good
    ) / max(usable_weight, 1e-9)
    direction = _direction(weighted)
    directional_scores = {
        minute: _directional(window.score, direction)
        for minute, window in windows.items()
        if window.quality == "ok"
    }
    supporting = sum(value >= 12.0 for value in directional_scores.values())
    opposing = sum(value <= -28.0 for value in directional_scores.values())
    agreement = supporting / max(1, len(good)) * 100.0

    primary = [windows[minute] for minute in (10, 20)]
    primary_valid = all(window.quality == "ok" for window in primary)
    primary_aligned = primary_valid and all(
        _directional(window.score, direction) >= 12.0
        for window in primary
    )
    primary_ratios = [
        float(window.volume_ratio)
        for window in primary
        if window.volume_ratio is not None
    ]
    volume_ok = (
        len(primary_ratios) == 2
        and primary_ratios[0] >= 1.05
        and primary_ratios[1] >= 0.85
        and statistics.mean(primary_ratios) >= 1.10
    )
    volume_score = (
        _clamp(
            52.0
            + math.log(max(statistics.mean(primary_ratios), 1e-9)) * 38.0
            + (10.0 if min(primary_ratios) >= 1.0 else 0.0)
        )
        if primary_ratios
        else 0.0
    )
    directional_weighted = _directional(weighted, direction)
    score = _clamp(
        directional_weighted * 0.62
        + agreement * 0.20
        + volume_score * 0.18
    )

    candidate = (
        len(good) == len(PRESSURE_WINDOWS)
        and directional_weighted >= 38.0
        and supporting >= 2
        and opposing == 0
        and primary_aligned
        and volume_ok
    )
    full_confirmation = (
        candidate
        and supporting == len(PRESSURE_WINDOWS)
        and directional_scores.get(10, 0.0) >= 18.0
        and directional_scores.get(20, 0.0) >= 15.0
        and directional_weighted >= 40.0
    )
    forming = (
        directional_weighted >= 15.0
        and supporting >= 2
        and not any(value <= -42.0 for value in directional_scores.values())
    )
    phase = "ready" if full_confirmation else (
        "strong" if candidate else ("forming" if forming else "none")
    )
    if phase == "none":
        score = min(score, 44.0)
    elif phase == "forming":
        score = min(score, 64.0)
    elif phase == "strong":
        score = min(score, 72.0)
    return Setup(
        "PRESSURE",
        direction=direction,
        score=score,
        phase=phase,
        volume_score=volume_score,
        confirmations=1 if candidate else 0,
        event_strength=directional_weighted,
        event_timestamp_ms=_timestamp_ms(candles[-1]) if candles else None,
        peak_score=score,
        reason=(
            f"weighted={weighted:.1f} agree={supporting}/{len(good)} "
            f"v10={windows[10].volume_ratio} v20={windows[20].volume_ratio}"
        ),
    )


def _assess_pressure(candles: list[Mapping[str, Any]]) -> Setup:
    current = _pressure_once(candles)
    if current.phase not in {"ready", "strong"}:
        return current
    confirmations = 1
    age = 0
    soft_gap = 0
    peak_score = current.score
    for offset in range(1, 9):
        if len(candles) <= offset:
            break
        previous = _pressure_once(candles[:-offset])
        if (
            previous.phase in {"ready", "strong"}
            and previous.direction == current.direction
        ):
            confirmations += 1
            age = offset
            soft_gap = 0
            peak_score = max(peak_score, previous.score)
        elif (
            previous.phase == "forming"
            and previous.direction == current.direction
            and soft_gap < 2
        ):
            age = offset
            soft_gap += 1
            peak_score = max(peak_score, previous.score)
        else:
            break
    current.confirmations = confirmations
    current.age_minutes = age
    current.peak_score = peak_score
    if candles:
        current.event_timestamp_ms = _timestamp_ms(candles[-1 - age])
    if current.phase == "strong" and confirmations >= 2:
        current.phase = "ready"
        current.score = _clamp(current.score + 5.0)
        current.peak_score = max(current.peak_score, current.score)
    return current


def _levels(book: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    result = []
    for row in book.get(side) or []:
        price = _f(row.get("price"))
        size = _f(row.get("remaining_base_amount", row.get("size")))
        if price > 0 and size > 0:
            result.append((price, size))
    result.sort(key=lambda level: level[0], reverse=side == "bids")
    return result


def _buy_base_for_quote(levels: list[tuple[float, float]], quote: float) -> float | None:
    remaining = quote
    base = 0.0
    for price, size in levels:
        take_quote = min(remaining, price * size)
        base += take_quote / price
        remaining -= take_quote
        if remaining <= 1e-9:
            return base
    return None


def _sell_base_for_quote(levels: list[tuple[float, float]], base: float) -> float | None:
    remaining = base
    received = 0.0
    for price, size in levels:
        take_base = min(remaining, size)
        received += take_base * price
        remaining -= take_base
        if remaining <= 1e-12:
            return received
    return None


def _roundtrip_cost(book: Mapping[str, Any], quote: float) -> float | None:
    if quote <= 0:
        return None
    base = _buy_base_for_quote(_levels(book, "asks"), quote)
    if base is None:
        return None
    received = _sell_base_for_quote(_levels(book, "bids"), base)
    if received is None:
        return None
    return max(0.0, (quote - received) / quote * 100.0)


def _series(candles: list[Mapping[str, Any]], count: int) -> list[Mapping[str, Any]]:
    rows = candles[-count:]
    if len(rows) < count or not _contiguous(rows):
        return []
    for row in rows:
        if min(_f(row.get(key)) for key in ("o", "h", "l", "c")) <= 0:
            return []
    return rows


def _robust_noise(candles: list[Mapping[str, Any]]) -> float:
    rows = candles[-190:-10] if len(candles) >= 200 else candles[:-10]
    closes = [_f(row.get("c")) for row in rows]
    returns = [
        abs(_pct(left, right))
        for left, right in zip(closes, closes[1:])
        if left > 0 and right > 0
    ]
    return max(0.015, statistics.median(returns) * 1.4826 if returns else 0.015)


def _linear_trend(closes: list[float]) -> tuple[float, float]:
    if len(closes) < 3 or min(closes) <= 0:
        return 0.0, 0.0
    values = [math.log(value) for value in closes]
    count = len(values)
    mean_x = (count - 1) / 2.0
    mean_y = statistics.mean(values)
    variance_x = sum((index - mean_x) ** 2 for index in range(count))
    if variance_x <= 0:
        return 0.0, 0.0
    slope = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(values)
    ) / variance_x
    fitted = [mean_y + slope * (index - mean_x) for index in range(count)]
    total = sum((value - mean_y) ** 2 for value in values)
    residual = sum((value - fit) ** 2 for value, fit in zip(values, fitted))
    r_squared = 0.0 if total <= 1e-15 else _clamp(1.0 - residual / total, 0.0, 1.0)
    return (math.exp(slope) - 1.0) * 100.0, r_squared


def _last_returns(rows: list[Mapping[str, Any]], count: int = 3) -> list[float]:
    closes = [_f(row.get("c")) for row in rows[-(count + 1):]]
    return [_pct(left, right) for left, right in zip(closes, closes[1:])]


def _baseline_volume(candles: list[Mapping[str, Any]], exclude: int = 12) -> float:
    rows = candles[-132:-exclude] if len(candles) >= 132 else candles[:-exclude]
    values = [_f(row.get("V")) for row in rows if _f(row.get("V")) > 0]
    return statistics.median(values) if values else 0.0


def _assess_trend(
    candles: list[Mapping[str, Any]],
    windows: Mapping[int, Window],
    noise: float,
) -> Setup:
    rows = _series(candles, 80)
    if not rows:
        return Setup("TREND", reason="insufficient contiguous history")
    closes = [_f(row.get("c")) for row in rows]
    ret_60 = _pct(closes[-61], closes[-1])
    ret_15 = _pct(closes[-16], closes[-1])
    slope_60, r_squared = _linear_trend(closes[-60:])
    z_60 = ret_60 / max(noise * math.sqrt(60), 1e-9)
    z_15 = ret_15 / max(noise * math.sqrt(15), 1e-9)
    context = z_60 * 0.67 + z_15 * 0.33
    direction = _direction(context)
    aligned = _direction(z_60) == _direction(z_15) and abs(z_15) >= 0.25
    context_strength = _clamp(
        abs(z_60) * 23.0
        + abs(z_15) * 16.0
        + r_squared * 32.0
        + (12.0 if aligned else -14.0)
        + min(12.0, abs(slope_60) / max(noise, 1e-9) * 8.0)
    )
    if abs(z_60) < 0.72 or context_strength < 42:
        return Setup(
            "TREND",
            direction=direction,
            score=context_strength * 0.65,
            phase="none",
            reason="no clear 60m trend",
        )

    lookback = rows[-12:]
    highs = [_f(row.get("h")) for row in lookback]
    lows = [_f(row.get("l")) for row in lookback]
    volumes = [_f(row.get("V")) for row in lookback]
    last_close = _f(lookback[-1].get("c"))
    event: tuple[float, int, int, float] | None = None
    for index in range(3, len(lookback)):
        before = range(max(0, index - 8), index)
        if direction > 0:
            anchor_index = max(before, key=lambda pos: highs[pos])
            anchor, extreme = highs[anchor_index], lows[index]
            move = _pct(anchor, extreme) * -1.0
        else:
            anchor_index = min(before, key=lambda pos: lows[pos])
            anchor, extreme = lows[anchor_index], highs[index]
            move = _pct(anchor, extreme)
        if move > 0 and (event is None or move > event[0]):
            event = (move, index, anchor_index, extreme)

    window_bias = _mean(
        _directional(windows[minute].score, direction)
        for minute in TREND_WINDOWS
        if minute in windows and windows[minute].quality == "ok"
    )
    forming_score = _clamp(context_strength * 0.72 + max(0.0, window_bias) * 0.18)
    if event is None:
        return Setup(
            "TREND",
            direction=direction,
            score=min(69.0, forming_score),
            phase="forming",
            volume_score=45.0,
            reason="trend without pullback",
        )

    pullback, event_index, anchor_index, extreme = event
    age = len(lookback) - 1 - event_index
    trend_move = max(abs(ret_60), noise * math.sqrt(60))
    retracement = pullback / max(trend_move, 1e-9)
    min_pullback = max(0.05, noise * 1.05)
    max_pullback = max(0.55, noise * 5.5)
    valid_pullback = (
        0 <= age <= 5
        and min_pullback <= pullback <= max_pullback
        and 0.07 <= retracement <= 0.58
    )

    if direction > 0:
        rebound = _pct(extreme, last_close)
    else:
        rebound = _pct(extreme, last_close) * -1.0
    rebound_fraction = rebound / max(pullback, 1e-9)
    recent_returns = _last_returns(lookback, 3)
    directional_returns = [_directional(value, direction) for value in recent_returns]
    confirmation_count = sum(value > 0 for value in directional_returns)
    last_confirms = bool(directional_returns and directional_returns[-1] > 0)

    baseline_volume = _baseline_volume(candles)
    pullback_slice = volumes[max(0, anchor_index + 1):event_index + 1]
    resume_slice = volumes[event_index + 1:]
    pullback_volume_ratio = _mean(pullback_slice) / baseline_volume if baseline_volume > 0 else 0.0
    resume_volume_ratio = _mean(resume_slice) / baseline_volume if baseline_volume > 0 and resume_slice else 0.0
    volume_score = _clamp(
        72.0
        + (1.05 - pullback_volume_ratio) * 55.0
        + (resume_volume_ratio - 0.75) * 35.0
    ) if baseline_volume > 0 else 0.0

    pullback_score = _clamp(100.0 - abs(retracement - 0.27) * 185.0)
    rebound_score = (
        _clamp(55.0 + rebound_fraction * 105.0)
        if rebound_fraction <= 0.58
        else _clamp(115.0 - rebound_fraction * 95.0)
    )
    trigger_score = _clamp(
        (45.0 if last_confirms else 0.0)
        + confirmation_count * 18.0
        + (18.0 if resume_volume_ratio >= max(0.75, pullback_volume_ratio) else 0.0)
    )
    score = _clamp(
        context_strength * 0.40
        + pullback_score * 0.23
        + rebound_score * 0.12
        + volume_score * 0.12
        + trigger_score * 0.13
    )

    too_deep = pullback > max_pullback or retracement > 0.72
    ready = (
        valid_pullback
        and 1 <= age <= 5
        and 0.10 <= rebound_fraction <= 0.72
        and last_confirms
        and confirmation_count >= 2
        and volume_score >= 42
        and context_strength >= 56
    )
    if too_deep:
        phase, exit_hint, score = "invalidated", True, min(score, 38.0)
    elif ready:
        phase, exit_hint = "ready", False
    else:
        phase, exit_hint, score = "forming", False, min(score, 69.0)
    return Setup(
        "TREND",
        direction=direction,
        score=score,
        phase=phase,
        volume_score=volume_score,
        age_minutes=age,
        exit_hint=exit_hint,
        confirmations=confirmation_count,
        event_strength=context_strength,
        event_timestamp_ms=_timestamp_ms(lookback[event_index]),
        reason=(
            f"pullback={pullback:.3f}% retrace={retracement:.2f} "
            f"rebound={rebound_fraction:.2f}"
        ),
    )


def _reversal_candidate(
    rows: list[Mapping[str, Any]],
    direction: int,
    noise: float,
    baseline_volume: float,
    minimum_move_pct: float,
) -> Setup:
    highs = [_f(row.get("h")) for row in rows]
    lows = [_f(row.get("l")) for row in rows]
    volumes = [_f(row.get("V")) for row in rows]
    last_close = _f(rows[-1].get("c"))
    best: tuple[float, float, int, int, float] | None = None

    for event_index in range(3, len(rows)):
        before = range(max(0, event_index - 7), event_index)
        if direction > 0:
            anchor_index = max(before, key=lambda pos: highs[pos])
            anchor, extreme = highs[anchor_index], lows[event_index]
            shock = _pct(anchor, extreme) * -1.0
        else:
            anchor_index = min(before, key=lambda pos: lows[pos])
            anchor, extreme = lows[anchor_index], highs[event_index]
            shock = _pct(anchor, extreme)
        duration = max(1, event_index - anchor_index)
        shock_z = shock / max(noise * math.sqrt(duration), 1e-9)
        if shock > 0 and (best is None or shock_z > best[0]):
            best = (shock_z, shock, event_index, anchor_index, extreme)

    if best is None:
        return Setup("REVERSAL")
    shock_z, shock, event_index, anchor_index, extreme = best
    age = len(rows) - 1 - event_index
    dynamic_minimum = max(minimum_move_pct, noise * math.sqrt(max(1, event_index - anchor_index)) * 2.65)
    if shock < dynamic_minimum or shock_z < 2.65:
        return Setup(
            "REVERSAL",
            direction=direction,
            score=min(42.0, _clamp(shock_z * 12.0)),
            phase="none",
            age_minutes=age,
            reason=f"shock only {shock:.3f}%/{shock_z:.2f}z",
        )

    rebound = _pct(extreme, last_close) * (1 if direction > 0 else -1)
    rebound_fraction = rebound / max(shock, 1e-9)
    recent_returns = _last_returns(rows, 3)
    directional_returns = [_directional(value, direction) for value in recent_returns]
    confirmation_count = sum(value > 0 for value in directional_returns)
    last_confirms = bool(directional_returns and directional_returns[-1] > 0)
    # Attribute volume to the actual shock candle. Including adjacent candles
    # lets the rebound borrow the shock's volume and can flip W the wrong way.
    event_volume = volumes[event_index]
    volume_ratio = event_volume / baseline_volume if baseline_volume > 0 else 0.0
    volume_score = _clamp((volume_ratio - 0.60) * 90.0) if baseline_volume > 0 else 0.0
    event_strength = shock_z * (
        1.0 + min(2.2, math.log1p(max(volume_ratio, 0.0)) * 0.42)
    )

    shock_score = _clamp(50.0 + (shock / max(dynamic_minimum, 1e-9) - 1.0) * 50.0)
    if 0.15 <= rebound_fraction <= 0.55:
        rebound_score = _clamp(72.0 + (rebound_fraction - 0.15) * 70.0)
    elif rebound_fraction < 0.15:
        rebound_score = _clamp(rebound_fraction / 0.15 * 72.0)
    else:
        rebound_score = _clamp(120.0 - rebound_fraction * 85.0)
    confirmation_score = _clamp(
        (45.0 if last_confirms else 0.0) + confirmation_count * 20.0
    )
    recency_score = {
        0: 100.0,
        1: 100.0,
        2: 92.0,
        3: 78.0,
        4: 55.0,
        5: 30.0,
    }.get(age, 0.0)
    score = _clamp(
        shock_score * 0.30
        + rebound_score * 0.25
        + volume_score * 0.20
        + confirmation_score * 0.15
        + recency_score * 0.10
    )

    stalled = (
        age > 0
        and len(directional_returns) >= 2
        and sum(directional_returns[-2:]) < -max(noise * 0.8, shock * 0.12)
    )
    event_close = _f(rows[event_index].get("c"))
    event_rebound = _pct(extreme, event_close) * (1 if direction > 0 else -1)
    event_rejection = event_rebound / max(shock, 1e-9)
    exceptional_rejection = (
        age == 0
        and shock_z >= 3.8
        and volume_ratio >= 4.0
        and 0.30 <= rebound_fraction <= 0.84
        and event_rejection >= 0.30
    )
    rejection_followthrough = (
        1 <= age <= 2
        and event_rejection >= 0.30
        and last_confirms
        and confirmation_count >= 1
        and volume_ratio >= 2.0
        and 0.12 <= rebound_fraction <= 0.88
    )
    ordinary_confirmation = (
        1 <= age <= 3
        and 0.13 <= rebound_fraction <= 0.82
        and last_confirms
        and confirmation_count >= 2
        and volume_ratio >= 2.00
        and event_strength >= 8.00
    )
    late = age > 4 or rebound_fraction > 0.92 or stalled
    ready = exceptional_rejection or rejection_followthrough or ordinary_confirmation
    if late:
        phase, exit_hint, score = "late", True, min(score, 35.0)
    elif ready:
        phase, exit_hint = "ready", False
    else:
        phase, exit_hint, score = "forming", False, min(score, 69.0)
    return Setup(
        "REVERSAL",
        direction=direction,
        score=score,
        phase=phase,
        volume_score=volume_score,
        age_minutes=age,
        exit_hint=exit_hint,
        confirmations=confirmation_count + (1 if exceptional_rejection else 0),
        event_strength=event_strength,
        event_timestamp_ms=_timestamp_ms(rows[event_index]),
        move_pct=shock,
        rejection_fraction=event_rejection,
        reason=(
            f"shock={shock:.3f}%/{shock_z:.2f}z "
            f"rebound={rebound_fraction:.2f} reject={event_rejection:.2f} "
            f"volume={volume_ratio:.2f}"
        ),
    )


def _assess_reversal(
    candles: list[Mapping[str, Any]],
    noise: float,
    minimum_move_pct: float,
) -> Setup:
    rows = _series(candles, 12)
    if not rows:
        return Setup("REVERSAL", reason="insufficient contiguous history")
    baseline_volume = _baseline_volume(candles)
    candidates = [
        _reversal_candidate(rows, 1, noise, baseline_volume, minimum_move_pct),
        _reversal_candidate(rows, -1, noise, baseline_volume, minimum_move_pct),
    ]
    # The dominant shock owns the direction for its short lifetime. This keeps
    # the counter-move from being reinterpreted as a fresh opposite shock.
    return max(
        candidates,
        key=lambda item: (
            item.event_strength,
            item.event_timestamp_ms or 0,
            item.score,
        ),
    )


def _activity_score(
    volume_24h: float,
    open_interest_usd: float,
    volume_oi: float | None,
    minimum_volume: float,
    minimum_oi: float,
) -> float:
    volume_score = _clamp(50.0 + 25.0 * math.log10(max(volume_24h, 1.0) / max(minimum_volume, 1.0)))
    oi_score = _clamp(50.0 + 25.0 * math.log10(max(open_interest_usd, 1.0) / max(minimum_oi, 1.0)))
    turnover_score = (
        0.0 if volume_oi is None
        else _clamp(52.0 + 24.0 * math.log10(max(volume_oi, 1e-6)))
    )
    return _clamp(volume_score * 0.45 + oi_score * 0.25 + turnover_score * 0.30)


def _setup_priority(item: Setup) -> tuple[int, float]:
    return (
        {
            "ready": 4,
            "strong": 3,
            "forming": 2,
            "late": 1,
            "invalidated": 1,
            "none": 0,
        }.get(item.phase, 0),
        item.score,
    )


def _detail_head(signal: Signal) -> str:
    if signal.state == "INVALID_DATA":
        return "⚫?"
    color = {
        "BUY": "🟣",
        "SELL": "🟣",
        "STRONG_LONG": "🟢",
        "STRONG_SHORT": "🔴",
        "WATCH_LONG": "🔵",
        "WATCH_SHORT": "🟠",
        "NO_TRADE": "🟡",
    }.get(signal.state, "⚫")
    return color + ("▲" if signal.direction >= 0 else "▼")


def _setup_code(signal: Signal) -> str:
    return {
        "PRESSURE": "P",
        "TREND": "T",
        "REVERSAL": "W",
    }.get(signal.selected_setup, "–")


def _window_color(window: Window) -> str:
    if window.quality != "ok":
        return "🟤"
    if window.score >= 15:
        return "🟢"
    if window.score <= -15:
        return "🔴"
    return "🟡"


def _setup_color(setup: Setup) -> str:
    if setup.phase in {"late", "invalidated"} or setup.exit_hint:
        return "🔴"
    if setup.phase == "ready" and setup.score >= 72:
        return "🟢"
    if setup.reason.startswith("insufficient"):
        return "🟤"
    return "🟡"


class LighterMonitor:
    def __init__(self, config: Mapping[str, Any], client: LighterClient | None = None) -> None:
        self.config = config
        self._validate_config()
        self.client = client or LighterClient(
            str(config.get("lighter_base_url", "https://mainnet.zklighter.elliot.ai/api/v1")),
            float(config.get("request_timeout_seconds", 15)),
            int(config.get("api_retry_count", 3)),
            int(config.get("closed_candle_delay_seconds", 8)),
        )

    def _validate_config(self) -> None:
        symbols = [str(value).upper() for value in self.config.get("candidate_symbols", [])]
        if not symbols or len(symbols) != len(set(symbols)):
            raise ValueError("candidate_symbols muss eindeutig und nicht leer sein")
        aliases = self.config.get("aliases") or {}
        invalid_aliases = [
            symbol
            for symbol in symbols
            if len(str(aliases.get(symbol, symbol[:3])).upper()) != 3
        ]
        if invalid_aliases:
            raise ValueError(f"Coin-Kürzel müssen drei Zeichen haben: {invalid_aliases}")
        summary_count = int(self.config.get("summary_coin_count", 5))
        minimum_details = int(self.config.get("minimum_detail_count", 2))
        maximum_details = int(self.config.get("maximum_detail_count", 4))
        if not 1 <= summary_count <= len(symbols):
            raise ValueError("summary_coin_count liegt außerhalb der Kandidatenzahl")
        if not 2 <= minimum_details <= maximum_details <= 4:
            raise ValueError("Detailzeilen müssen zwischen zwei und vier liegen")
        if int(self.config.get("discord_max_codepoints_per_line", 34)) < summary_count * 4 + 3:
            raise ValueError("Discord-Zeilenlimit ist für die Top-Zeile zu klein")
        watch = float(self.config.get("watch_trade_readiness", 50))
        strong = float(self.config.get("strong_trade_readiness", 66))
        immediate = float(self.config.get("immediate_trade_readiness", 74))
        pressure_immediate = float(
            self.config.get("pressure_immediate_trade_readiness", immediate)
        )
        if not (
            0 < watch < strong < immediate <= 100
            and strong < pressure_immediate <= immediate
        ):
            raise ValueError("Readiness-Schwellen sind nicht logisch geordnet")
        funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
        funding_hard = float(self.config.get("funding_hard_hourly_pct", 0.05))
        if not 0 <= funding_watch < funding_hard:
            raise ValueError("Funding-Schwellen sind nicht logisch geordnet")
        setup_thresholds = (
            "pressure_minimum_setup_score",
            "trend_minimum_setup_score",
            "trend_minimum_context_strength",
            "trend_minimum_volume_score",
            "reversal_minimum_setup_score",
        )
        if any(
            not 0 <= float(self.config.get(key, 100)) <= 100
            for key in setup_thresholds
        ):
            raise ValueError("Setup-Schwellen müssen zwischen null und 100 liegen")
        minimum_reversal = float(
            self.config.get("hard_reversal_min_move_pct", 0.20)
        )
        immediate_reversal = float(
            self.config.get("reversal_immediate_min_move_pct", 0.75)
        )
        rejection = float(
            self.config.get("reversal_min_rejection_fraction", 0.30)
        )
        if not 0 < minimum_reversal <= immediate_reversal or not 0 <= rejection <= 1:
            raise ValueError("Wende-Schwellen sind nicht logisch geordnet")
        positive_keys = (
            "execution_quote_usdc",
            "minimum_volume_24h_usdc",
            "minimum_open_interest_usdc",
            "max_roundtrip_cost_pct",
            "candle_count",
            "parallel_requests",
            "request_timeout_seconds",
        )
        if any(float(self.config.get(key, 0)) <= 0 for key in positive_keys):
            raise ValueError("Ausführungs- und Datenparameter müssen positiv sein")
        if int(self.config.get("candle_count", 360)) < 200:
            raise ValueError("candle_count muss mindestens 200 betragen")
        if int(self.config.get("pressure_entry_max_age_minutes", 2)) < 0:
            raise ValueError("pressure_entry_max_age_minutes darf nicht negativ sein")

    def _analyse(
        self,
        market: Mapping[str, Any],
        funding: float | None,
        candles: list[Mapping[str, Any]],
        book: Mapping[str, Any],
    ) -> Signal:
        symbol = str(market["symbol"]).upper()
        aliases = self.config.get("aliases") or {}
        signal = Signal(symbol=symbol, alias=str(aliases.get(symbol, symbol[:3])).upper())
        signal.volume_24h = _f(market.get("daily_quote_token_volume"))
        reference_price = _f(
            market.get("mark_price"),
            _f(market.get("last_trade_price")),
        )
        signal.open_interest_usd = _f(market.get("open_interest")) * reference_price
        signal.volume_oi = (
            signal.volume_24h / signal.open_interest_usd
            if signal.open_interest_usd > 0
            else None
        )
        signal.funding_hourly_pct = None if funding is None else funding * 100.0
        execution_quote = float(self.config.get("execution_quote_usdc", 50))
        signal.cost_pct = _roundtrip_cost(book, execution_quote)
        signal.windows = {
            minutes: _window(candles, minutes)
            for minutes in ANALYSIS_WINDOWS
        }
        display_good = [
            signal.windows[minutes]
            for minutes in DISPLAY_WINDOWS
            if signal.windows[minutes].quality == "ok"
        ]
        signal.data_quality = len(display_good) / len(DISPLAY_WINDOWS) * 100.0
        if len(display_good) < 2:
            signal.reasons.append("zu wenige gültige Fenster")
            return signal

        minimum_volume = float(self.config.get("minimum_volume_24h_usdc", 500_000))
        minimum_oi = float(self.config.get("minimum_open_interest_usdc", 100_000))
        signal.activity_score = _activity_score(
            signal.volume_24h,
            signal.open_interest_usd,
            signal.volume_oi,
            minimum_volume,
            minimum_oi,
        )
        signal.liquidity_score = signal.activity_score
        cost_limit = float(self.config.get("max_roundtrip_cost_pct", 0.15))
        signal.execution_score = (
            0.0
            if signal.cost_pct is None
            else _clamp(100.0 - signal.cost_pct / max(cost_limit, 1e-9) * 100.0)
        )

        noise = _robust_noise(candles)
        signal.pressure = _assess_pressure(candles)
        signal.trend = _assess_trend(candles, signal.windows, noise)
        signal.reversal = _assess_reversal(
            candles,
            noise,
            float(self.config.get("hard_reversal_min_move_pct", 0.20)),
        )
        setups = (signal.pressure, signal.trend, signal.reversal)
        selected = max(
            setups,
            key=lambda item: (
                *_setup_priority(item),
                2 if item.kind == "REVERSAL" and item.phase == "ready" else 0,
                1 if item.kind == "PRESSURE" else 0,
            ),
        )
        signal.selected_setup = selected.kind if selected.phase != "none" else "NONE"
        raw_setup = selected.score if selected.phase != "none" else min(selected.score, 42.0)
        valid_volume_ratios = [
            item.volume_ratio
            for item in display_good
            if item.volume_ratio is not None
        ]
        general_volume_score = (
            _clamp(50.0 + math.log(max(_mean(valid_volume_ratios), 1e-9)) * 45.0)
            if valid_volume_ratios
            else 0.0
        )
        signal.volume_confirmation = (
            selected.volume_score
            if selected.phase != "none"
            else general_volume_score
        )

        if selected.direction and selected.phase != "none":
            signal.direction = max(1.0, selected.score) * selected.direction
        else:
            weights = {5: 0.52, 20: 0.30, 60: 0.18}
            usable_weight = sum(weights[item.minutes] for item in display_good)
            signal.direction = (
                sum(
                    item.score * weights[item.minutes]
                    for item in display_good
                ) / usable_weight
                if usable_weight
                else 0.0
            )

        direction = _direction(signal.direction)
        active_opposites = [
            item
            for item in setups
            if item is not selected
            and item.phase in {"ready", "strong"}
            and item.direction
            and item.direction != direction
        ]
        setup_conflict = any(
            item.phase == "ready" and item.score >= selected.score - 12.0
            for item in active_opposites
        )
        countertrend_reversal = (
            selected.kind == "REVERSAL"
            and selected.phase == "ready"
            and signal.trend.direction
            and signal.trend.direction != direction
            and signal.trend.phase in {"ready", "strong", "forming"}
            and signal.trend.score >= 62.0
            and selected.rejection_fraction < 0.25
        )
        setup_conflict = setup_conflict or countertrend_reversal
        same_direction_support = sum(
            item.phase in {"ready", "strong", "forming"}
            and item.direction == direction
            for item in setups
        )

        movement = _mean(
            min(
                100.0,
                abs(item.price_pct or 0.0) * 55.0
                + abs(math.log(max(item.volume_ratio or 1.0, 1e-9))) * 38.0,
            )
            for item in display_good
        )
        stability = _clamp(
            48.0
            + selected.confirmations * 14.0
            + (18.0 if selected.phase == "ready" else 0.0)
            + (8.0 if same_direction_support >= 2 else 0.0)
        )
        if (
            selected.kind == "REVERSAL"
            and selected.phase == "ready"
            and selected.event_strength >= 7.0
        ):
            stability = max(stability, 88.0)
        signal.opportunity = _clamp(
            raw_setup * 0.52
            + signal.activity_score * 0.13
            + signal.execution_score * 0.12
            + movement * 0.15
            + stability * 0.08
        )
        signal.confidence = _clamp(
            raw_setup * 0.52
            + signal.data_quality * 0.12
            + signal.execution_score * 0.11
            + signal.activity_score * 0.09
            + signal.volume_confirmation * 0.08
            + stability * 0.08
        )
        signal.trade_readiness = _clamp(
            raw_setup * 0.57
            + signal.volume_confirmation * 0.12
            + signal.data_quality * 0.09
            + signal.execution_score * 0.08
            + signal.activity_score * 0.07
            + stability * 0.07
            + (4.0 if same_direction_support >= 2 else 0.0)
            - (15.0 if setup_conflict else 0.0)
        )

        funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
        funding_hard = float(self.config.get("funding_hard_hourly_pct", 0.05))
        funding_against = (
            signal.funding_hourly_pct is not None
            and _directional(signal.funding_hourly_pct, direction) > 0
        )
        funding_pressure = (
            _directional(signal.funding_hourly_pct or 0.0, direction)
            if funding_against
            else 0.0
        )
        if funding_pressure > funding_watch:
            setup_factor = {
                "PRESSURE": 1.0,
                "TREND": 0.8,
                "REVERSAL": 0.25,
            }.get(selected.kind, 0.6)
            signal.trade_readiness -= _clamp(
                (funding_pressure - funding_watch)
                / max(funding_hard - funding_watch, 1e-9)
                * 14.0
                * setup_factor,
                0.0,
                14.0,
            )
        signal.trade_readiness = _clamp(signal.trade_readiness)

        executable = signal.cost_pct is not None and signal.cost_pct <= cost_limit
        meets_market_minimum = execution_quote >= _f(market.get("min_quote_amount"))
        enough_volume = signal.volume_24h >= minimum_volume
        enough_oi = signal.open_interest_usd >= minimum_oi
        funding_block = (
            funding_pressure > funding_hard
            and selected.kind != "REVERSAL"
        )
        all_display_windows = len(display_good) == len(DISPLAY_WINDOWS)
        immediate_threshold = float(self.config.get("immediate_trade_readiness", 74))
        if selected.kind == "PRESSURE":
            immediate_threshold = float(
                self.config.get(
                    "pressure_immediate_trade_readiness",
                    immediate_threshold,
                )
            )
        strong_threshold = float(self.config.get("strong_trade_readiness", 66))
        watch_threshold = float(self.config.get("watch_trade_readiness", 50))
        if selected.kind == "REVERSAL":
            fresh_entry = (
                selected.age_minutes is None
                or selected.age_minutes <= 3
            )
        elif selected.kind == "PRESSURE":
            fresh_entry = (
                selected.age_minutes is None
                or selected.age_minutes <= int(
                    self.config.get("pressure_entry_max_age_minutes", 5)
                )
            )
        else:
            fresh_entry = (
                selected.age_minutes is None
                or selected.age_minutes <= 3
            )
        setup_minimum = float(
            self.config.get(
                {
                    "PRESSURE": "pressure_minimum_setup_score",
                    "TREND": "trend_minimum_setup_score",
                    "REVERSAL": "reversal_minimum_setup_score",
                }.get(selected.kind, ""),
                100.0,
            )
        )
        setup_is_precise = selected.score >= setup_minimum
        if selected.kind == "PRESSURE":
            if not setup_is_precise:
                hold_margin = float(
                    self.config.get("pressure_hold_score_margin", 7)
                )
                setup_is_precise = (
                    bool(selected.age_minutes)
                    and selected.peak_score >= setup_minimum
                    and selected.score >= setup_minimum - hold_margin
                )
        elif selected.kind == "TREND":
            setup_is_precise = (
                setup_is_precise
                and selected.event_strength >= float(
                    self.config.get("trend_minimum_context_strength", 84)
                )
                and selected.volume_score >= float(
                    self.config.get("trend_minimum_volume_score", 50)
                )
            )
        elif selected.kind == "REVERSAL":
            setup_is_precise = (
                setup_is_precise
                and selected.move_pct >= float(
                    self.config.get("reversal_immediate_min_move_pct", 0.75)
                )
                and selected.rejection_fraction >= float(
                    self.config.get("reversal_min_rejection_fraction", 0.30)
                )
            )
        hard_block = (
            not executable
            or not meets_market_minimum
            or not enough_volume
            or not enough_oi
            or funding_block
        )

        if not executable or not meets_market_minimum:
            signal.state = "NO_TRADE"
            signal.reasons.append("Orderbuchkosten blockieren")
        elif not enough_volume or not enough_oi:
            signal.state = "NO_TRADE"
            signal.reasons.append("Liquidität/OI blockiert")
        elif funding_block:
            signal.state = "NO_TRADE"
            signal.reasons.append("Funding blockiert Richtung")
        elif (
            selected.phase == "ready"
            and all_display_windows
            and fresh_entry
            and setup_is_precise
            and not setup_conflict
            and signal.trade_readiness >= immediate_threshold
        ):
            signal.state = "BUY" if direction > 0 else "SELL"
            signal.reasons.append(f"{_setup_code(signal)} sofort bereit")
        elif (
            not hard_block
            and selected.phase in {"ready", "strong"}
            and signal.trade_readiness >= strong_threshold
        ):
            signal.state = "STRONG_LONG" if direction > 0 else "STRONG_SHORT"
            signal.reasons.append(f"{_setup_code(signal)} stark, nicht sofort")
        elif (
            not hard_block
            and selected.phase in {"ready", "strong", "forming"}
            and signal.trade_readiness >= watch_threshold
        ):
            signal.state = "WATCH_LONG" if direction > 0 else "WATCH_SHORT"
            signal.reasons.append(f"{_setup_code(signal)} im Aufbau")
        else:
            signal.state = "NO_TRADE"
            signal.reasons.append("keine sichere Einstiegsfreigabe")

        if setup_conflict:
            signal.reasons.append("Setups widersprechen sich")
        if selected.exit_hint:
            signal.reasons.append("Setup abgelaufen/aussteigen prüfen")
        if funding_pressure > funding_watch and not funding_block:
            signal.reasons.append("Funding gegen Richtung")
        return signal

    def _apply_btc_context(self, signals: list[Signal]) -> None:
        """Use BTC only as a risk gate; it never creates or upgrades a signal."""
        btc = next(
            (
                item
                for item in signals
                if item.symbol == "BTC" and item.state != "INVALID_DATA"
            ),
            None,
        )
        if btc is None:
            return
        btc_bias = btc.direction
        for item in signals:
            if item.state == "INVALID_DATA":
                continue
            if item.symbol == "BTC":
                item.btc_context = 100.0
                continue
            if abs(btc_bias) < 15.0:
                item.btc_context = 60.0
                continue
            alignment = _directional(btc_bias, _direction(item.direction))
            item.btc_context = (
                100.0 if alignment >= 55.0 else
                82.0 if alignment >= 25.0 else
                60.0 if alignment > -18.0 else
                38.0 if alignment > -50.0 else
                18.0
            )
            if item.btc_context >= 40.0:
                continue
            penalty = 7.0 if item.selected_setup == "REVERSAL" else 12.0
            item.trade_readiness = _clamp(item.trade_readiness - penalty)
            item.confidence = _clamp(item.confidence - penalty * 0.55)
            reversal_overrides_btc = (
                item.selected_setup == "REVERSAL"
                and item.reversal.move_pct >= float(
                    self.config.get("reversal_btc_override_move_pct", 1.0)
                )
            )
            if item.state in {"BUY", "SELL"} and not reversal_overrides_btc:
                item.state = (
                    "STRONG_LONG"
                    if item.direction >= 0
                    else "STRONG_SHORT"
                )
                item.reasons.append("BTC-Kontext verhindert Sofortfreigabe")

    @staticmethod
    def _rank(signals: list[Signal]) -> list[Signal]:
        return sorted(
            signals,
            key=lambda item: (
                -STATE_TIER.get(item.state, 0),
                -item.trade_readiness,
                -item.confidence,
                -item.opportunity,
                -abs(item.direction),
                item.alias,
            ),
        )

    def _format(self, signals: list[Signal], now: datetime) -> str:
        ranked = self._rank(signals)
        summary_count = int(self.config.get("summary_coin_count", 5))
        summary = ranked[:summary_count]
        timestamp = now.astimezone(
            ZoneInfo(str(self.config.get("timezone", "Europe/Berlin")))
        ).strftime(":%M")
        lines = [
            "".join(
                f"{item.alias}{SUMMARY_COLORS[item.state]}"
                for item in reversed(summary)
            )
            + timestamp
        ]

        minimum_details = int(self.config.get("minimum_detail_count", 2))
        maximum_details = int(self.config.get("maximum_detail_count", 4))
        details = list(ranked[:minimum_details])
        for item in ranked[minimum_details:maximum_details]:
            if STATE_TIER.get(item.state, 0) >= 2:
                details.append(item)
            else:
                break
        for item in details:
            windows = "".join(
                f"{minutes}{_window_color(item.windows.get(minutes, Window(minutes)))}"
                for minutes in DISPLAY_WINDOWS
            )
            volume_color = (
                "🟤" if item.volume_confirmation <= 0 else
                ("🟢" if item.volume_confirmation >= 62 else
                 ("🟡" if item.volume_confirmation >= 38 else "🔴"))
            )
            cost_limit = float(self.config.get("max_roundtrip_cost_pct", 0.15))
            cost_color = (
                "🟤" if item.cost_pct is None else
                ("🟢" if item.cost_pct <= cost_limit * 0.42 else
                 ("🟡" if item.cost_pct <= cost_limit else "🔴"))
            )
            liquidity_color = (
                "🟢" if item.liquidity_score >= 58 else
                ("🟡" if item.liquidity_score >= 40 else "🔴")
            )
            btc_color = (
                "🟤" if item.btc_context is None else
                ("🟢" if item.btc_context >= 72 else
                 ("🟡" if item.btc_context >= 40 else "🔴"))
            )
            funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
            funding_hard = float(self.config.get("funding_hard_hourly_pct", 0.05))
            if item.funding_hourly_pct is None:
                funding_color = "🟤"
            else:
                against = _directional(
                    item.funding_hourly_pct,
                    _direction(item.direction),
                )
                funding_color = (
                    "🔴" if against > funding_hard else
                    ("🟡" if against > funding_watch else "🟢")
                )
            line = (
                f"{_detail_head(item)}{_setup_code(item)} {windows} "
                f"V{volume_color}L{liquidity_color}B{btc_color}"
                f"K{cost_color}F{funding_color} {item.alias}"
            )
            lines.append(line)

        max_len = int(self.config.get("discord_max_codepoints_per_line", 34))
        if any(len(line) > max_len for line in lines):
            raise RuntimeError("Discord-Zeilenlimit überschritten")
        return "\n".join(lines)

    def run(self) -> tuple[str, dict[str, Any]]:
        now = datetime.now(timezone.utc)
        allowed = {str(value).upper() for value in self.config.get("candidate_symbols", [])}
        markets = {
            str(row.get("symbol")).upper(): row
            for row in self.client.markets()
            if str(row.get("symbol")).upper() in allowed
            and str(row.get("status")).lower() == "active"
            and str(row.get("market_type")).lower() == "perp"
        }
        try:
            raw_funding = self.client.funding()
        except Exception:
            raw_funding = []
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
                pool.submit(self._load_one, row): symbol
                for symbol, row in markets.items()
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    candles, book = future.result()
                    signals.append(
                        self._analyse(
                            markets[symbol],
                            funding.get(symbol),
                            candles,
                            book,
                        )
                    )
                except Exception as exc:
                    aliases = self.config.get("aliases") or {}
                    signals.append(
                        Signal(
                            symbol=symbol,
                            alias=str(aliases.get(symbol, symbol[:3])).upper(),
                            reasons=[f"{type(exc).__name__}: {exc}"],
                        )
                    )
        for symbol in sorted(allowed - set(markets)):
            signals.append(
                Signal(
                    symbol,
                    str((self.config.get("aliases") or {}).get(symbol, symbol[:3])).upper(),
                    reasons=["kein aktiver Lighter-Krypto-Perp"],
                )
            )

        self._apply_btc_context(signals)
        report = self._format(signals, now)
        minimum_lines = int(self.config.get("minimum_detail_count", 2)) + 1
        maximum_lines = int(self.config.get("maximum_detail_count", 4)) + 1
        if not minimum_lines <= len(report.splitlines()) <= maximum_lines:
            raise RuntimeError("Discord-Ausgabe hat nicht drei bis fünf Zeilen")
        payload = {
            "version": APP_VERSION,
            "package_revision": PACKAGE_REVISION,
            "generated_at": now.isoformat(),
            "report": report,
            "signals": [asdict(item) for item in self._rank(signals)],
        }
        return report, payload

    def _load_one(
        self,
        market: Mapping[str, Any],
    ) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
        market_id = int(market["market_id"])
        candle_count = int(self.config.get("candle_count", 360))
        return self.client.candles(market_id, count=candle_count), self.client.book(market_id)


# Package revision: v3.6.3-ptw-precision-r3
