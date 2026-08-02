"""Lighter-native early-swing/T/W signal engine for CF v3.9.3."""
from __future__ import annotations

import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from regime_context import calculate_regimes
from extremity_context import calculate_extremity, extremity_code, extremity_color

APP_VERSION = "3.9.3"
PACKAGE_REVISION = "v3.9.3-lighter-top-pool-r1"
ANALYSIS_WINDOWS = (5, 10, 15, 20, 60)
DISPLAY_WINDOWS = (5, 20, 60)
TREND_WINDOWS = (5, 15, 60)
# Kept as the stable public display contract.
WINDOWS = DISPLAY_WINDOWS
GLOBAL_BTC_EVENT_KINDS = {"FOMC", "CPI", "NFP", "PPI", "GDP", "PCE", "EXPIRY", "ETF"}

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
    recovery_fraction: float = 0.0
    peak_score: float = 0.0
    expected_edge_pct: float = 0.0
    structural_reclaim: bool = False
    relative_confirmed: bool = True
    relative_participation: float | None = None
    relative_opposition: bool = False
    new_extreme_after_event: bool = False
    reclaim_level: float | None = None
    invalidation_price: float | None = None
    boundary_distance_pct: float = 0.0
    approach_confirmed: bool = False
    clean_boundary_test: bool = False
    preview_only: bool = False
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
    tape_quality: float = 0.0
    volume_coverage: float = 0.0
    volume_spike_share: float = 1.0
    attention_score: float = 0.0
    btc_context: float | None = None
    cost_pct: float | None = None
    funding_hourly_pct: float | None = None
    volume_24h: float = 0.0
    open_interest_usd: float = 0.0
    volume_oi: float | None = None
    price: float = 0.0
    candle_timestamp_ms: int = 0
    noise_pct: float = 0.0
    min_quote_amount: float = 0.0
    platform_max_leverage: float = 0.0
    maintenance_margin_pct: float = 0.0
    taker_fee_pct: float = 0.0
    candidate_tier: str = "core"
    candidate_penalty: float = 0.0
    extremity_available: bool = False
    extremity_score: float = 0.0
    extremity_confidence: float = 0.0
    extremity_momentum: float = 0.0
    extremity_vwap: float = 0.0
    extremity_range: float = 0.0
    extremity_funding: float = 0.0
    extremity_regime_adjustment: float = 0.0
    extremity_intraday: float = 0.0
    extremity_swing: float = 0.0
    extremity_swing_available: bool = False
    extremity_return_1d: float | None = None
    extremity_return_3d: float | None = None
    extremity_return_7d: float | None = None
    technical_stop_price: float | None = None
    technical_stop_pct: float | None = None
    regime_available: bool = False
    regime_score: float = 0.0
    regime_consistency: float = 0.0
    regime_modifier: float = 0.0
    return_7d: float | None = None
    return_14d: float | None = None
    return_30d: float | None = None
    relative_7d: float | None = None
    relative_14d: float | None = None
    relative_30d: float | None = None
    btc_rebound_pct: float | None = None
    rebound_participation: float | None = None
    relative_drift_60m: float | None = None
    event_code: str = ""
    event_display_code: str = ""
    event_kind: str = ""
    event_title: str = ""
    event_priority: float = 0.0
    event_risk: float = 0.0
    event_block_new: bool = False
    event_leverage_cap: int | None = None
    event_source_name: str = ""
    event_source_url: str = ""
    event_starts_at: str | None = None
    chase_warning: bool = False
    chase_blocked: bool = False
    windows: dict[int, Window] = field(default_factory=dict)
    early: Setup = field(default_factory=lambda: Setup("EARLY"))
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

    def daily_candles(self, market_id: int, count: int = 40) -> list[Mapping[str, Any]]:
        now = int(time.time())
        payload = self.get(
            "/candles",
            market_id=market_id,
            resolution="1d",
            start_timestamp=now - (count + 5) * 86_400,
            end_timestamp=now,
            count_back=min(500, max(35, count)),
            set_timestamp_to_end="true",
        )
        rows_by_time: dict[int, Mapping[str, Any]] = {}
        for row in list(payload.get("c") or []):
            stamp = _timestamp_ms(row)
            if stamp > 0 and _f(row.get("c")) > 0:
                rows_by_time[stamp] = row
        return [rows_by_time[key] for key in sorted(rows_by_time)][-count:]

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
    flat_tolerance = max(0.04, expected_move * 0.32)

    # v3.7.1: Price determines direction. Quote volume is deliberately only a
    # bounded strength modifier because candle volume has no buyer/seller sign.
    # A large volume spike with flat price must therefore never become a Long
    # or Short signal by itself.
    volume_factor = _clamp(
        1.0 + math.log(max(ratio, 1e-9)) * 0.22,
        0.72,
        1.28,
    )
    flat_factor = _clamp(abs(price) / flat_tolerance, 0.0, 1.0)
    score = price_units * 30.0 * volume_factor * flat_factor
    return Window(minutes, price, ratio, _clamp(score, -100.0, 100.0), "ok", "")



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



def _tape_quality(candles: list[Mapping[str, Any]], count: int = 90) -> tuple[float, float, float]:
    """Measure whether one-minute quote volume is continuous rather than gap/spike driven."""
    rows = candles[-count:]
    if len(rows) < min(60, count) or not _contiguous(rows):
        return 0.0, 0.0, 1.0
    volumes = [_f(row.get("V")) for row in rows]
    positive = [value for value in volumes if value > 0]
    coverage = len(positive) / len(volumes)
    if not positive:
        return 0.0, coverage, 1.0
    baseline = statistics.median(positive)
    recent = volumes[-20:]
    recent_total = sum(recent)
    spike_share = max(recent, default=0.0) / recent_total if recent_total > 0 else 1.0
    quiet_fraction = sum(
        value <= 0 or value < baseline * 0.035
        for value in volumes
    ) / len(volumes)
    extreme_ratio = max(recent, default=0.0) / max(baseline, 1e-9)
    coverage_score = coverage * 52.0
    concentration_score = (1.0 - _clamp((spike_share - 0.14) / 0.56, 0.0, 1.0)) * 25.0
    continuity_score = (1.0 - _clamp(quiet_fraction / 0.36, 0.0, 1.0)) * 18.0
    extreme_penalty = _clamp((extreme_ratio - 22.0) / 38.0, 0.0, 1.0) * 10.0
    return _clamp(coverage_score + concentration_score + continuity_score - extreme_penalty), coverage, spike_share


def _assess_early(
    candles: list[Mapping[str, Any]],
    noise: float,
    *,
    current_price: float | None,
    max_age_minutes: int,
    max_consumed_fraction: float,
    minimum_efficiency: float,
    minimum_volume_consistency: int,
    approach_noise_multiple: float,
    probe_noise_multiple: float,
    probe_max_consumed_fraction: float,
) -> Setup:
    """Find a fresh expansion from compression before most of the expected move is consumed."""
    rows = _series(candles, 90)
    if not rows:
        return Setup("EARLY", reason="insufficient contiguous history")
    baseline_volume = _baseline_volume(candles, exclude=8)
    if baseline_volume <= 0:
        return Setup("EARLY", reason="insufficient quote-volume baseline")

    pre = rows[-42:-7]
    trigger = rows[-7:]
    older = rows[-82:-42]
    pre_high = max(_f(row.get("h")) for row in pre)
    pre_low = min(_f(row.get("l")) for row in pre)
    older_high = max(_f(row.get("h")) for row in older)
    older_low = min(_f(row.get("l")) for row in older)
    pre_range = max(0.0, _pct(pre_low, pre_high))
    older_range = max(pre_range, _pct(older_low, older_high))
    pre_returns = _last_returns(pre, min(20, len(pre) - 1))
    older_returns = _last_returns(older, min(25, len(older) - 1))
    pre_noise = statistics.median(abs(value) for value in pre_returns) if pre_returns else noise
    older_noise = statistics.median(abs(value) for value in older_returns) if older_returns else noise
    range_ratio = pre_range / max(older_range, noise, 1e-9)
    noise_ratio = pre_noise / max(older_noise, noise * 0.35, 1e-9)
    compression_score = _clamp(
        52.0
        + (1.0 - _clamp(range_ratio, 0.0, 1.4)) * 30.0
        + (1.0 - _clamp(noise_ratio, 0.0, 1.5)) * 24.0
    )

    closes = [_f(row.get("c")) for row in trigger]
    volumes = [_f(row.get("V")) for row in trigger]
    returns = [_pct(left, right) for left, right in zip(closes, closes[1:])]
    target_move = max(0.16, pre_range * 0.82, noise * math.sqrt(20.0) * 3.6)
    live_price = _f(current_price) if current_price is not None else closes[-1]
    live_gap = abs(_pct(closes[-1], live_price)) if live_price > 0 else 999.0
    if live_gap > max(0.60, target_move * 2.0):
        live_price = closes[-1]
    candidates: list[Setup] = []
    for direction in (1, -1):
        boundary = pre_high if direction > 0 else pre_low
        directional_returns = [_directional(value, direction) for value in returns]
        move_3 = sum(directional_returns[-3:])
        move_6 = sum(directional_returns)
        first_3 = sum(directional_returns[:3])
        acceleration = move_3 - first_3
        path = sum(abs(value) for value in returns)
        efficiency = max(0.0, move_6) / max(path, 1e-9)
        extension = _directional(_pct(boundary, closes[-1]), direction)
        live_extension = _directional(_pct(boundary, live_price), direction)
        breakout_floor = max(0.022, noise * 0.42)
        approach_band = max(0.075, noise * approach_noise_multiple)
        probe_band = max(0.020, noise * probe_noise_multiple)
        crossings: list[int] = []
        for index, close in enumerate(closes):
            current = _directional(_pct(boundary, close), direction)
            previous = (
                _directional(_pct(boundary, closes[index - 1]), direction)
                if index > 0 else -999.0
            )
            if current >= breakout_floor and previous < breakout_floor:
                crossings.append(index)
        event_index = crossings[-1] if crossings else None
        age = None if event_index is None else len(closes) - 1 - event_index
        consumed = max(0.0, extension) / max(target_move, 1e-9)
        expected_edge = max(0.0, target_move - max(0.0, extension))

        ratios = [value / baseline_volume for value in volumes[-4:]]
        consistency = sum(value >= 1.05 for value in ratios)
        recent_ratio = _mean(ratios)
        recent_total = sum(volumes[-5:])
        spike_share = max(volumes[-5:], default=0.0) / recent_total if recent_total > 0 else 1.0
        volume_score = _clamp(
            38.0
            + (recent_ratio - 1.0) * 36.0
            + consistency * 10.0
            - max(0.0, spike_share - 0.58) * 90.0
        )
        momentum_floor = max(0.035, noise * 1.15)
        momentum_score = _clamp(
            42.0
            + (move_3 / max(momentum_floor, 1e-9) - 1.0) * 24.0
            + (move_6 / max(momentum_floor * 1.55, 1e-9) - 1.0) * 18.0
            + acceleration / max(noise, 1e-9) * 5.0
        )
        efficiency_score = _clamp(
            45.0 + (efficiency - minimum_efficiency) * 100.0
        )
        recency_score = 58.0 if age is None else {
            0: 100.0, 1: 94.0, 2: 84.0, 3: 62.0, 4: 38.0
        }.get(age, 15.0)
        room_score = _clamp((1.0 - consumed / max(max_consumed_fraction, 1e-9)) * 100.0)
        score = _clamp(
            compression_score * 0.18
            + momentum_score * 0.27
            + volume_score * 0.20
            + efficiency_score * 0.17
            + recency_score * 0.10
            + room_score * 0.08
        )
        positive_candles = sum(value > 0 for value in directional_returns[-4:])
        closed_near = -approach_band <= extension < breakout_floor
        live_near = -approach_band <= live_extension < breakout_floor
        approach_progress = (
            move_3 >= momentum_floor * 0.28
            and efficiency >= minimum_efficiency * 0.55
            and recent_ratio >= 0.92
            and consistency >= 1
            and positive_candles >= 2
            and compression_score >= 48.0
            and acceleration >= -noise * 0.45
        )
        approach_confirmed = bool((closed_near or live_near) and approach_progress)
        clean_test = (
            (age is None or age <= 1)
            and extension >= -probe_band
            and extension <= max(breakout_floor * 2.2, target_move * probe_max_consumed_fraction)
            and consumed <= probe_max_consumed_fraction
            and move_3 >= momentum_floor * 0.55
            and move_6 >= momentum_floor * 0.90
            and efficiency >= minimum_efficiency * 0.72
            and recent_ratio >= 1.02
            and consistency >= max(2, minimum_volume_consistency - 1)
            and positive_candles >= 3
            and compression_score >= 50.0
            and spike_share <= 0.62
            and expected_edge >= target_move * 0.45
        )
        late = (
            extension > 0
            and (
                (age is not None and age > max_age_minutes)
                or consumed > max_consumed_fraction
                or efficiency < minimum_efficiency * 0.72
                or move_3 <= 0
            )
        )
        ready = (
            event_index is not None
            and age is not None
            and age <= max_age_minutes
            and consumed <= max_consumed_fraction
            and efficiency >= minimum_efficiency
            and consistency >= minimum_volume_consistency
            and recent_ratio >= 1.12
            and move_3 >= momentum_floor
            and move_6 >= momentum_floor * 1.45
            and acceleration >= 0.0
            and compression_score >= 50.0
            and positive_candles >= 3
            and spike_share <= 0.62
        )
        forming = (
            event_index is None
            and extension < breakout_floor
            and live_extension < breakout_floor
            and approach_confirmed
            and score >= 52.0
            and expected_edge >= target_move * 0.38
        )
        if late:
            phase, exit_hint, score = "late", True, min(score, 39.0)
        elif ready:
            phase, exit_hint = "ready", False
        elif clean_test:
            phase, exit_hint, score = "strong", False, min(max(score, 64.0), 80.0)
        elif forming:
            phase, exit_hint, score = "forming", False, min(max(score, 55.0), 72.0)
        else:
            phase, exit_hint, score = "none", False, min(score, 48.0)
        candidates.append(Setup(
            "EARLY",
            direction=direction,
            score=score,
            phase=phase,
            volume_score=volume_score,
            age_minutes=age,
            exit_hint=exit_hint,
            confirmations=positive_candles,
            event_strength=momentum_score,
            event_timestamp_ms=(
                _timestamp_ms(trigger[event_index])
                if event_index is not None else None
            ),
            move_pct=max(0.0, extension),
            rejection_fraction=efficiency,
            recovery_fraction=consumed,
            peak_score=compression_score,
            expected_edge_pct=expected_edge,
            invalidation_price=boundary * (
                1.0 - direction * max(0.015, noise * 0.65) / 100.0
            ),
            boundary_distance_pct=max(0.0, -max(extension, live_extension)),
            approach_confirmed=approach_confirmed,
            clean_boundary_test=clean_test,
            preview_only=bool(phase == "forming" and live_near and not closed_near),
            reason=(
                f"compression={compression_score:.1f} move3={move_3:.3f}% "
                f"eff={efficiency:.2f} volume={recent_ratio:.2f}/{consistency} "
                f"age={age} used={consumed:.2f} edge={expected_edge:.3f}% "
                f"dist={max(0.0, -max(extension, live_extension)):.3f}% "
                f"test={int(clean_test)} preview={int(live_near and not closed_near)}"
            ),
        ))
    return max(candidates, key=lambda item: (*_setup_priority(item), item.event_strength))


def _assess_trend_once(
    candles: list[Mapping[str, Any]],
    windows: Mapping[int, Window],
    noise: float,
    config: Mapping[str, Any],
) -> Setup:
    """Detect only a quick dip/bounce inside a clear continuing trend.

    Price determines direction. Unsigned quote volume is used only to confirm
    that market activity remains elevated; it never supplies direction.
    """
    rows = _series(candles, 130)
    if not rows:
        return Setup("TREND", reason="insufficient contiguous history")
    closes = [_f(row.get("c")) for row in rows]
    volumes_all = [_f(row.get("V")) for row in rows]
    ret_60 = _pct(closes[-61], closes[-1])
    ret_15 = _pct(closes[-16], closes[-1])
    slope_60, r_squared = _linear_trend(closes[-60:])
    z_60 = ret_60 / max(noise * math.sqrt(60), 1e-9)
    z_15 = ret_15 / max(noise * math.sqrt(15), 1e-9)
    direction = _direction(z_60)
    directional_z_15 = _directional(z_15, direction)
    directional_slope = _directional(slope_60, direction) / max(noise, 1e-9)

    previous_60 = _mean(volumes_all[-120:-60])
    recent_60 = _mean(volumes_all[-60:])
    previous_20 = _mean(volumes_all[-40:-20])
    recent_20 = _mean(volumes_all[-20:])
    volume_ratio_60 = recent_60 / previous_60 if previous_60 > 0 else 0.0
    volume_ratio_20 = recent_20 / previous_20 if previous_20 > 0 else 0.0
    baseline_volume = _mean(volumes_all[-80:-20])
    recent_active = sum(
        value >= baseline_volume * 0.85
        for value in volumes_all[-6:]
    ) if baseline_volume > 0 else 0
    minimum_ratio_60 = float(config.get("trend_min_volume_ratio_60", 1.08))
    minimum_ratio_20 = float(config.get("trend_min_volume_ratio_20", 1.03))
    activity_trend = (
        volume_ratio_60 >= minimum_ratio_60
        and volume_ratio_20 >= minimum_ratio_20
        and recent_active >= 4
    )

    aligned = directional_z_15 >= 0.25
    context_strength = _clamp(
        abs(z_60) * 23.0
        + max(0.0, directional_z_15) * 16.0
        - max(0.0, -directional_z_15) * 26.0
        + r_squared * 32.0
        + (10.0 if aligned else -18.0)
        + min(10.0, max(0.0, directional_slope) * 7.0)
        - min(12.0, max(0.0, -directional_slope) * 10.0)
        + _clamp((volume_ratio_60 - 1.0) * 55.0, -12.0, 12.0)
        + _clamp((volume_ratio_20 - 1.0) * 45.0, -10.0, 10.0)
    )
    if abs(z_60) < 0.82 or context_strength < 48 or not activity_trend:
        return Setup(
            "TREND",
            direction=direction,
            score=min(48.0, context_strength * 0.58),
            phase="none",
            volume_score=_clamp(50.0 + (volume_ratio_60 - 1.0) * 70.0),
            reason=(
                f"no continuing price/activity trend v60={volume_ratio_60:.2f} "
                f"v20={volume_ratio_20:.2f} active={recent_active}/6"
            ),
        )

    lookback = rows[-10:]
    local_closes = [_f(row.get("c")) for row in lookback]
    volumes = [_f(row.get("V")) for row in lookback]
    last_close = local_closes[-1]
    max_duration = max(1, int(config.get("trend_max_pullback_duration_minutes", 3)))
    candidates: list[tuple[float, int, int, float]] = []
    # A normal candle wick is not a dip. T requires a real counter-trend close
    # and at least one completed resume candle after the local extreme.
    local_returns = _last_returns(lookback, len(lookback) - 1)
    for event_index in range(2, len(lookback) - 1):
        before_positions = list(range(max(0, event_index - max_duration), event_index))
        if not before_positions:
            continue
        if direction > 0:
            anchor_index = max(before_positions, key=lambda pos: local_closes[pos])
            anchor, extreme = local_closes[anchor_index], local_closes[event_index]
            move = -_pct(anchor, extreme)
            counter_move = local_returns[event_index - 1] < 0
        else:
            anchor_index = min(before_positions, key=lambda pos: local_closes[pos])
            anchor, extreme = local_closes[anchor_index], local_closes[event_index]
            move = _pct(anchor, extreme)
            counter_move = local_returns[event_index - 1] > 0
        if move > 0 and counter_move:
            candidates.append((move, event_index, anchor_index, extreme))
    if not candidates:
        return Setup(
            "TREND",
            direction=direction,
            score=min(52.0, context_strength * 0.62),
            phase="none",
            volume_score=_clamp(55.0 + (volume_ratio_60 - 1.0) * 65.0),
            reason="continuing trend without quick dip",
        )

    # Prefer a recent valid dip over an older, larger move.
    pullback, event_index, anchor_index, extreme = max(
        candidates,
        key=lambda item: (item[1], item[0]),
    )
    age = len(lookback) - 1 - event_index
    duration = event_index - anchor_index
    trend_move = max(abs(ret_60), noise * math.sqrt(60))
    retracement = pullback / max(trend_move, 1e-9)
    min_pullback = max(0.05, noise * 1.05)
    max_pullback = max(0.46, noise * 4.8)
    max_age = max(0, int(config.get("trend_max_pullback_age_minutes", 2)))
    valid_pullback = (
        0 <= age <= max_age
        and 1 <= duration <= max_duration
        and min_pullback <= pullback <= max_pullback
        and 0.07 <= retracement <= 0.50
    )

    rebound = _pct(extreme, last_close) if direction > 0 else _pct(extreme, last_close) * -1.0
    rebound_fraction = rebound / max(pullback, 1e-9)
    expected_edge = max(
        max(0.0, pullback - max(0.0, rebound)),
        noise * math.sqrt(5.0) * 1.7,
    )
    recent_returns = _last_returns(lookback, 3)
    directional_returns = [_directional(value, direction) for value in recent_returns]
    confirmation_count = sum(value > 0 for value in directional_returns)
    last_confirms = bool(directional_returns and directional_returns[-1] > 0)

    pullback_slice = volumes[max(0, anchor_index + 1):event_index + 1]
    resume_slice = volumes[event_index + 1:]
    pullback_volume_ratio = _mean(pullback_slice) / baseline_volume if baseline_volume > 0 else 0.0
    resume_volume_ratio = _mean(resume_slice) / baseline_volume if baseline_volume > 0 and resume_slice else 0.0
    max_pullback_volume = float(config.get("trend_max_pullback_volume_ratio", 1.05))
    min_resume_volume = float(config.get("trend_min_resume_volume_ratio", 0.90))
    volume_structure_ok = (
        pullback_volume_ratio <= max_pullback_volume
        and resume_volume_ratio >= min_resume_volume
        and resume_volume_ratio >= pullback_volume_ratio * 0.95
    )
    volume_score = _clamp(
        45.0
        + (volume_ratio_60 - 1.0) * 80.0
        + (volume_ratio_20 - 1.0) * 65.0
        + (1.0 - pullback_volume_ratio) * 30.0
        + (resume_volume_ratio - 0.85) * 35.0
    )

    middle_scores = [
        _directional(windows[minute].score, direction)
        for minute in (10, 15, 20)
        if minute in windows and windows[minute].quality == "ok"
    ]
    middle_supporting = sum(value >= 5.0 for value in middle_scores)
    middle_opposing = sum(value <= -18.0 for value in middle_scores)
    middle_confirmed = (
        len(middle_scores) == 3
        and middle_supporting >= 2
        and middle_opposing == 0
        and _mean(middle_scores) >= 6.0
    )
    pullback_score = _clamp(100.0 - abs(retracement - 0.24) * 210.0)
    rebound_score = _clamp(100.0 - abs(rebound_fraction - 0.30) * 180.0)
    trigger_score = _clamp(
        (42.0 if last_confirms else 0.0)
        + confirmation_count * 18.0
        + (18.0 if volume_structure_ok else 0.0)
    )
    score = min(90.0, _clamp(
        context_strength * 0.36
        + pullback_score * 0.22
        + rebound_score * 0.12
        + volume_score * 0.17
        + trigger_score * 0.13
    ))

    too_deep = pullback > max_pullback or retracement > 0.62
    ready = (
        valid_pullback
        and 0.12 <= rebound_fraction <= 0.58
        and last_confirms
        and confirmation_count >= 2
        and volume_structure_ok
        and volume_score >= float(config.get("trend_minimum_volume_score", 58))
        and context_strength >= 60
        and middle_confirmed
    )
    if too_deep:
        phase, exit_hint, score = "invalidated", True, min(score, 36.0)
    elif ready:
        phase, exit_hint = "ready", False
    else:
        phase, exit_hint, score = "forming", False, min(score, 65.0)
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
        expected_edge_pct=expected_edge,
        invalidation_price=extreme * (
            1.0 - direction * max(0.015, noise * 0.65) / 100.0
        ),
        reason=(
            f"quick-dip={pullback:.3f}% age={age} duration={duration} "
            f"retrace={retracement:.2f} rebound={rebound_fraction:.2f} "
            f"v60={volume_ratio_60:.2f} v20={volume_ratio_20:.2f} "
            f"dipV={pullback_volume_ratio:.2f} resumeV={resume_volume_ratio:.2f}"
        ),
    )


def _assess_trend(
    candles: list[Mapping[str, Any]],
    windows: Mapping[int, Window],
    noise: float,
    config: Mapping[str, Any],
) -> Setup:
    return _assess_trend_once(candles, windows, noise, config)


def _reversal_candidate(
    rows: list[Mapping[str, Any]],
    direction: int,
    noise: float,
    baseline_volume: float,
    minimum_move_pct: float,
    config: Mapping[str, Any],
) -> Setup:
    highs = [_f(row.get("h")) for row in rows]
    lows = [_f(row.get("l")) for row in rows]
    closes = [_f(row.get("c")) for row in rows]
    volumes = [_f(row.get("V")) for row in rows]
    last_close = closes[-1]
    best: tuple[float, float, int, int, float, float] | None = None

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
        # The largest actual shock owns the W event. A smaller later counter-
        # candle cannot reset age or reverse the interpretation.
        if shock > 0 and (
            best is None or (shock, shock_z) > (best[0], best[1])
        ):
            best = (shock, shock_z, event_index, anchor_index, extreme, anchor)

    if best is None:
        return Setup("REVERSAL")
    shock, shock_z, event_index, anchor_index, extreme, anchor = best
    age = len(rows) - 1 - event_index
    dynamic_minimum = max(
        minimum_move_pct,
        noise * math.sqrt(max(1, event_index - anchor_index)) * 2.65,
    )
    if shock < dynamic_minimum or shock_z < 2.65:
        return Setup(
            "REVERSAL",
            direction=direction,
            score=min(42.0, _clamp(shock_z * 12.0)),
            phase="none",
            age_minutes=age,
            reason=f"shock only {shock:.3f}%/{shock_z:.2f}z",
        )

    event_close = closes[event_index]
    post_rows = rows[event_index + 1:]
    post_closes = closes[event_index + 1:]
    rebound = _pct(extreme, last_close) * (1 if direction > 0 else -1)
    rebound_fraction = rebound / max(shock, 1e-9)

    post_returns: list[float] = []
    previous = event_close
    for close in post_closes:
        post_returns.append(_directional(_pct(previous, close), direction))
        previous = close
    recent_directional = post_returns[-3:]
    confirmation_count = sum(value > max(noise * 0.08, 0.005) for value in recent_directional)
    last_confirms = bool(recent_directional and recent_directional[-1] > max(noise * 0.05, 0.003))

    # Attribute volume only to the actual shock candle. Quote volume has no
    # buyer/seller sign, so it may confirm importance but never direction.
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

    reclaim_fraction_required = float(
        config.get("reversal_structural_reclaim_fraction", 0.24)
    )
    reclaim_distance = abs(anchor - extreme) * reclaim_fraction_required
    reclaim_level = (
        extreme + reclaim_distance
        if direction > 0
        else extreme - reclaim_distance
    )
    close_reclaimed = (
        last_close >= max(event_close, reclaim_level)
        if direction > 0
        else last_close <= min(event_close, reclaim_level)
    )
    tolerance_pct = max(
        0.002,
        noise * float(config.get("reversal_new_extreme_tolerance_noise", 0.18)),
    )
    if direction > 0:
        new_extreme = any(
            _f(row.get("l")) < extreme * (1.0 - tolerance_pct / 100.0)
            for row in post_rows
        )
    else:
        new_extreme = any(
            _f(row.get("h")) > extreme * (1.0 + tolerance_pct / 100.0)
            for row in post_rows
        )
    structural_reclaim = bool(
        age >= int(config.get("reversal_min_post_event_closes", 1))
        and close_reclaimed
        and rebound_fraction >= reclaim_fraction_required
        and last_confirms
        and not new_extreme
    )

    event_rebound = _pct(extreme, event_close) * (1 if direction > 0 else -1)
    event_rejection = event_rebound / max(shock, 1e-9)
    exceptional_rejection = (
        age == 0
        and shock_z >= 3.8
        and volume_ratio >= 4.0
        and 0.30 <= rebound_fraction <= 0.84
        and event_rejection >= 0.30
    )
    followthrough = (
        1 <= age <= 3
        and last_confirms
        and confirmation_count >= 1
        and volume_ratio >= 1.6
        and event_strength >= 6.8
        and 0.12 <= rebound_fraction <= 0.88
    )
    stalled = (
        age > 0
        and len(recent_directional) >= 2
        and sum(recent_directional[-2:]) < -max(noise * 0.8, shock * 0.12)
    )
    late = age > 4 or rebound_fraction > 0.92 or stalled

    if new_extreme:
        phase, exit_hint, score = "invalidated", True, min(score, 24.0)
    elif late:
        phase, exit_hint, score = "late", True, min(score, 35.0)
    elif structural_reclaim and followthrough:
        phase, exit_hint = "ready", False
    elif exceptional_rejection or followthrough:
        # Visible as W?: a real rejection/follow-through exists, but the
        # structurally important reclaim has not yet happened.
        phase, exit_hint = "strong", False
        score = min(score, 78.0)
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
        recovery_fraction=rebound_fraction,
        structural_reclaim=structural_reclaim,
        relative_confirmed=True,
        new_extreme_after_event=new_extreme,
        reclaim_level=reclaim_level,
        invalidation_price=extreme * (
            1.0 - direction * max(0.002, tolerance_pct) / 100.0
        ),
        reason=(
            f"shock={shock:.3f}%/{shock_z:.2f}z "
            f"rebound={rebound_fraction:.2f} reject={event_rejection:.2f} "
            f"reclaim={int(structural_reclaim)} newExtreme={int(new_extreme)} "
            f"volume={volume_ratio:.2f}"
        ),
    )

def _assess_reversal(
    candles: list[Mapping[str, Any]],
    noise: float,
    minimum_move_pct: float,
    config: Mapping[str, Any],
) -> Setup:
    rows = _series(candles, 12)
    if not rows:
        return Setup("REVERSAL", reason="insufficient contiguous history")
    baseline_volume = _baseline_volume(candles)
    candidates = [
        _reversal_candidate(rows, 1, noise, baseline_volume, minimum_move_pct, config),
        _reversal_candidate(rows, -1, noise, baseline_volume, minimum_move_pct, config),
    ]
    # The dominant shock owns the direction for its short lifetime. This keeps
    # the counter-move from being reinterpreted as a fresh opposite shock.
    return max(
        candidates,
        key=lambda item: (
            item.move_pct,
            item.event_strength,
            -(item.event_timestamp_ms or 0),
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
    if signal.selected_setup == "REVERSAL":
        setup = signal.reversal
        return "W" if (
            setup.phase == "ready"
            and setup.structural_reclaim
            and setup.relative_confirmed
            and not setup.new_extreme_after_event
        ) else "W?"
    return {
        "EARLY": "E",
        "TREND": "T",
    }.get(signal.selected_setup, "+" if signal.direction >= 0 else "-")


def _setup_age_code(signal: Signal) -> str:
    setup = _selected_setup(signal)
    if signal.selected_setup not in {"EARLY", "TREND", "REVERSAL"} or setup.age_minutes is None:
        return ""
    return f"a{max(0, min(9, int(setup.age_minutes)))}"


def _selected_setup(signal: Signal) -> Setup:
    return {
        "EARLY": signal.early,
        "TREND": signal.trend,
        "REVERSAL": signal.reversal,
    }.get(signal.selected_setup, Setup("NONE"))


def _action_code(signal: Signal) -> str:
    if signal.chase_warning:
        return "WAIT"
    return {
        "BUY": "NOW",
        "SELL": "NOW",
        "STRONG_LONG": "TRY",
        "STRONG_SHORT": "TRY",
        "WATCH_LONG": "NEAR",
        "WATCH_SHORT": "NEAR",
        "NO_TRADE": "WAIT",
        "INVALID_DATA": "DATA",
    }.get(signal.state, "WAIT")


def _market_bias(signal: Signal) -> float | None:
    weights = {5: 0.45, 20: 0.35, 60: 0.20}
    valid = [
        signal.windows[minute]
        for minute in DISPLAY_WINDOWS
        if minute in signal.windows and signal.windows[minute].quality == "ok"
    ]
    usable_weight = sum(weights[item.minutes] for item in valid)
    if len(valid) < 2 or usable_weight <= 0:
        return None
    return sum(
        item.score * weights[item.minutes]
        for item in valid
    ) / usable_weight


def _directional_window_agreement(signal: Signal, direction: int) -> tuple[int, int]:
    threshold = max(0.01, signal.noise_pct * 0.30)
    aligned = 0
    opposed = 0
    for minutes in DISPLAY_WINDOWS:
        window = signal.windows.get(minutes)
        if window is None or window.quality != "ok" or window.price_pct is None:
            continue
        directional_move = _directional(window.price_pct, direction)
        if directional_move >= threshold:
            aligned += 1
        elif directional_move <= -threshold:
            opposed += 1
    return aligned, opposed


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
        self.last_signals: list[Signal] = []
        self.last_snapshots: dict[str, dict[str, Any]] = {}
        self.generated_at = datetime.now(timezone.utc)

    def _validate_config(self) -> None:
        symbols = [str(value).upper() for value in self.config.get("candidate_symbols", [])]
        if not symbols or len(symbols) != len(set(symbols)):
            raise ValueError("candidate_symbols muss eindeutig und nicht leer sein")
        aliases = self.config.get("aliases") or {}
        invalid_aliases = [
            symbol for symbol in symbols
            if len(str(aliases.get(symbol, symbol[:3])).upper()) != 3
        ]
        if invalid_aliases:
            raise ValueError(f"Coin-Kürzel müssen drei Zeichen haben: {invalid_aliases}")
        summary_count = int(self.config.get("summary_coin_count", 5))
        summary_anchor = str(self.config.get("summary_anchor_symbol", "BTC")).upper()
        minimum_details = int(self.config.get("minimum_detail_count", 1))
        maximum_details = int(self.config.get("maximum_detail_count", 4))
        if not 1 <= summary_count <= len(symbols):
            raise ValueError("summary_coin_count liegt außerhalb der Kandidatenzahl")
        if summary_anchor not in symbols:
            raise ValueError("summary_anchor_symbol muss im Kandidatenpool liegen")
        if not 1 <= minimum_details <= maximum_details <= 4:
            raise ValueError("Detailzeilen müssen zwischen eins und vier liegen")
        detail_limit = int(self.config.get("discord_max_codepoints_per_line", 34))
        header_limit = int(self.config.get("discord_header_max_codepoints", detail_limit))
        if not 34 <= detail_limit <= 2_000:
            raise ValueError("Discord-Detailzeilenlimit ist ungueltig")
        if not summary_count * 5 - 1 <= header_limit <= 2_000:
            raise ValueError("Discord-Zeilenlimit ist für die Top-Zeile zu klein")
        watch = float(self.config.get("watch_trade_readiness", 56))
        strong = float(self.config.get("strong_trade_readiness", 64))
        immediate = float(self.config.get("immediate_trade_readiness", 76))
        if not 0 < watch < strong < immediate <= 100:
            raise ValueError("Readiness-Schwellen sind nicht logisch geordnet")
        funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
        funding_hard = float(self.config.get("funding_hard_hourly_pct", 0.05))
        if not 0 <= funding_watch < funding_hard:
            raise ValueError("Funding-Schwellen sind nicht logisch geordnet")
        setup_thresholds = (
            "early_minimum_setup_score",
            "early_minimum_volume_score",
            "trend_minimum_setup_score",
            "trend_minimum_context_strength",
            "trend_minimum_volume_score",
            "reversal_minimum_setup_score",
            "btc_early_immediate_breadth_score",
            "btc_trend_immediate_breadth_score",
            "btc_reversal_immediate_breadth_score",
            "minimum_tape_quality",
            "detail_attention_threshold",
        )
        if any(not 0 <= float(self.config.get(key, 100)) <= 100 for key in setup_thresholds):
            raise ValueError("Setup- und Qualitätswerte müssen zwischen null und 100 liegen")
        if not 0 < float(self.config.get("early_max_consumed_fraction", 0.66)) < 1:
            raise ValueError("early_max_consumed_fraction muss zwischen null und eins liegen")
        if not 0 < float(self.config.get("early_min_efficiency", 0.52)) <= 1:
            raise ValueError("early_min_efficiency muss zwischen null und eins liegen")
        early_max_age = int(self.config.get("early_max_age_minutes", 2))
        if not 0 <= early_max_age <= 4:
            raise ValueError("early_max_age_minutes muss zwischen null und vier liegen")
        immediate_age = int(self.config.get("early_immediate_max_age_minutes", 1))
        immediate_used = float(self.config.get("early_immediate_max_consumed_fraction", 0.50))
        if not 0 <= immediate_age <= early_max_age:
            raise ValueError("early_immediate_max_age_minutes ist ungültig")
        if not 0 < immediate_used <= float(self.config.get("early_max_consumed_fraction", 0.66)):
            raise ValueError("early_immediate_max_consumed_fraction ist ungültig")
        approach_multiple = float(self.config.get("early_approach_noise_multiple", 2.0))
        probe_multiple = float(self.config.get("early_probe_noise_multiple", 0.60))
        probe_used = float(self.config.get("early_probe_max_consumed_fraction", 0.35))
        if not 0.8 <= approach_multiple <= 4.0:
            raise ValueError("early_approach_noise_multiple ist ungültig")
        if not 0.2 <= probe_multiple < approach_multiple:
            raise ValueError("early_probe_noise_multiple ist ungültig")
        if not 0.10 <= probe_used < immediate_used:
            raise ValueError("early_probe_max_consumed_fraction ist ungültig")
        extremity_warning = float(self.config.get("extremity_chase_warning", 55.0))
        extremity_block = float(self.config.get("extremity_chase_block", 72.0))
        extremity_penalty = float(self.config.get("extremity_chase_penalty", 8.0))
        if not 0 < extremity_warning < extremity_block <= 100 or not 0 <= extremity_penalty <= 20:
            raise ValueError("Extremity-Schwellen sind nicht logisch geordnet")
        early_chase_warning = float(self.config.get("early_chase_warning", 35.0))
        early_chase_block = float(self.config.get("early_chase_block", 50.0))
        early_chase_penalty = float(self.config.get("early_chase_penalty", 10.0))
        if not 0 < early_chase_warning < early_chase_block <= 100 or not 0 <= early_chase_penalty <= 20:
            raise ValueError("E-Anti-Chase-Schwellen sind nicht logisch geordnet")
        if not 0 <= float(self.config.get("reversal_now_min_extremity", 18.0)) <= 60:
            raise ValueError("W-Extremitätsminimum ist ungültig")
        if not 1 <= int(self.config.get("early_min_volume_consistency", 2)) <= 4:
            raise ValueError("early_min_volume_consistency muss zwischen eins und vier liegen")
        minimum_reversal = float(self.config.get("hard_reversal_min_move_pct", 0.20))
        immediate_reversal = float(self.config.get("reversal_immediate_min_move_pct", 0.75))
        rejection = float(self.config.get("reversal_min_rejection_fraction", 0.30))
        max_recovery = float(self.config.get("reversal_max_entry_recovery_fraction", 0.68))
        late_override = float(self.config.get("reversal_late_recovery_override_move_pct", 1.0))
        reversal_confirmations = int(
            self.config.get("reversal_immediate_min_confirmations", 2)
        )
        reversal_agreement = int(
            self.config.get("reversal_immediate_min_window_agreement", 2)
        )
        reversal_opposed = int(
            self.config.get("reversal_immediate_max_opposed_windows", 1)
        )
        reclaim_fraction = float(self.config.get("reversal_structural_reclaim_fraction", 0.24))
        new_extreme_tolerance = float(self.config.get("reversal_new_extreme_tolerance_noise", 0.18))
        post_event_closes = int(self.config.get("reversal_min_post_event_closes", 1))
        if (
            not 0 < minimum_reversal <= immediate_reversal <= late_override
            or not 0 <= rejection <= 1
            or not 0 < max_recovery < 1
            or not 1 <= reversal_confirmations <= 3
            or not 1 <= reversal_agreement <= len(DISPLAY_WINDOWS)
            or not 0 <= reversal_opposed < len(DISPLAY_WINDOWS)
            or not 0.10 <= reclaim_fraction <= 0.60
            or not 0 <= new_extreme_tolerance <= 1.0
            or not 1 <= post_event_closes <= 3
        ):
            raise ValueError("Wende-Schwellen sind nicht logisch geordnet")
        positive_keys = (
            "execution_quote_usdc", "minimum_volume_24h_usdc",
            "minimum_open_interest_usdc", "max_roundtrip_cost_pct",
            "candle_count", "parallel_requests", "request_timeout_seconds",
            "early_minimum_expected_edge_pct", "early_cost_edge_multiple",
        )
        if any(float(self.config.get(key, 0)) <= 0 for key in positive_keys):
            raise ValueError("Ausführungs- und Datenparameter müssen positiv sein")
        if int(self.config.get("candle_count", 360)) < 200:
            raise ValueError("candle_count muss mindestens 200 betragen")
        breadth_symbols = [str(value).upper() for value in self.config.get("btc_breadth_symbols", [])]
        if len(set(breadth_symbols)) < 3 or "BTC" in breadth_symbols:
            raise ValueError("btc_breadth_symbols braucht mindestens drei Nicht-BTC-Märkte")
        if not set(breadth_symbols).issubset(set(symbols)):
            raise ValueError("btc_breadth_symbols müssen im Kandidatenpool liegen")
        regime = self.config.get("regime") or {}
        if bool(regime.get("enabled", True)):
            horizons = [int(value) for value in regime.get("horizons_days", [7, 14, 30])]
            if horizons != [7, 14, 30]:
                raise ValueError("Regime-Horizonte müssen exakt 7/14/30 Tage sein")
            maximum_modifier = float(regime.get("maximum_modifier_points", 10.0))
            if not 0 < maximum_modifier <= 10:
                raise ValueError("Regime-Modifier muss zwischen 0 und 10 liegen")

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
        signal.price = (
            _f(candles[-1].get("c"), reference_price)
            if candles
            else reference_price
        )
        signal.candle_timestamp_ms = _timestamp_ms(candles[-1]) if candles else 0
        signal.min_quote_amount = max(0.0, _f(market.get("min_quote_amount")))
        minimum_margin_fraction = _f(market.get("min_initial_margin_fraction"))
        signal.platform_max_leverage = (
            10_000.0 / minimum_margin_fraction
            if minimum_margin_fraction > 0
            else 0.0
        )
        signal.maintenance_margin_pct = max(
            0.0,
            _f(market.get("maintenance_margin_fraction")) / 100.0,
        )
        signal.taker_fee_pct = (
            max(0.0, _f(market.get("taker_fee")))
            if market.get("is_taker_fee_enabled", True) is not False
            else 0.0
        )
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
        signal.tape_quality, signal.volume_coverage, signal.volume_spike_share = _tape_quality(candles)
        signal.liquidity_score = _clamp(
            signal.activity_score * 0.72 + signal.tape_quality * 0.28
        )
        cost_limit = float(self.config.get("max_roundtrip_cost_pct", 0.15))
        signal.execution_score = (
            0.0
            if signal.cost_pct is None
            else _clamp(100.0 - signal.cost_pct / max(cost_limit, 1e-9) * 100.0)
        )

        noise = _robust_noise(candles)
        signal.noise_pct = noise
        signal.early = _assess_early(
            candles,
            noise,
            current_price=reference_price,
            max_age_minutes=int(self.config.get("early_max_age_minutes", 2)),
            max_consumed_fraction=float(self.config.get("early_max_consumed_fraction", 0.66)),
            minimum_efficiency=float(self.config.get("early_min_efficiency", 0.52)),
            minimum_volume_consistency=int(self.config.get("early_min_volume_consistency", 2)),
            approach_noise_multiple=float(self.config.get("early_approach_noise_multiple", 2.0)),
            probe_noise_multiple=float(self.config.get("early_probe_noise_multiple", 0.60)),
            probe_max_consumed_fraction=float(self.config.get("early_probe_max_consumed_fraction", 0.35)),
        )
        signal.trend = _assess_trend(candles, signal.windows, noise, self.config)
        signal.reversal = _assess_reversal(
            candles,
            noise,
            float(self.config.get("hard_reversal_min_move_pct", 0.20)),
            self.config,
        )
        setups = (signal.early, signal.trend, signal.reversal)
        selected = max(
            setups,
            key=lambda item: (
                *_setup_priority(item),
                3 if item.kind == "EARLY" and item.phase == "ready" else
                2 if item.kind == "REVERSAL" and item.phase == "ready" else 0,
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
        invalidation = selected.invalidation_price
        if invalidation is not None and signal.price > 0:
            valid_side = (
                (direction > 0 and invalidation < signal.price)
                or (direction < 0 and invalidation > signal.price)
            )
            if valid_side:
                signal.technical_stop_price = float(invalidation)
                signal.technical_stop_pct = abs(_pct(signal.price, float(invalidation)))
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
            min(100.0, abs(item.price_pct or 0.0) * 82.0)
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
            + signal.activity_score * 0.10
            + signal.execution_score * 0.10
            + movement * 0.10
            + stability * 0.08
            + signal.tape_quality * 0.10
        )
        signal.confidence = _clamp(
            raw_setup * 0.46
            + signal.data_quality * 0.10
            + signal.execution_score * 0.10
            + signal.activity_score * 0.07
            + signal.volume_confirmation * 0.08
            + stability * 0.10
            + signal.tape_quality * 0.09
        )
        signal.trade_readiness = _clamp(
            raw_setup * 0.53
            + signal.volume_confirmation * 0.10
            + signal.data_quality * 0.06
            + signal.execution_score * 0.08
            + signal.activity_score * 0.05
            + stability * 0.07
            + signal.tape_quality * 0.11
            + (4.0 if same_direction_support >= 2 else 0.0)
            - (15.0 if setup_conflict else 0.0)
        )
        if selected.kind == "TREND":
            signal.opportunity = _clamp(
                signal.opportunity - float(self.config.get("trend_attention_penalty", 5)) * 0.4
            )
            signal.confidence = _clamp(
                signal.confidence - float(self.config.get("trend_confidence_penalty", 3))
            )
            signal.trade_readiness = _clamp(
                signal.trade_readiness - float(self.config.get("trend_readiness_penalty", 5))
            )

        test_symbols = {
            str(value).upper()
            for value in self.config.get("test_candidate_symbols", [])
        }
        penalties = self.config.get("candidate_trade_penalty_points") or {}
        signal.candidate_tier = "test" if symbol in test_symbols else "core"
        signal.candidate_penalty = _clamp(
            _f(penalties.get(symbol)),
            0.0,
            20.0,
        )
        if signal.candidate_penalty > 0:
            signal.opportunity = _clamp(
                signal.opportunity - signal.candidate_penalty * 0.75
            )
            signal.confidence = _clamp(
                signal.confidence - signal.candidate_penalty
            )
            signal.trade_readiness = _clamp(
                signal.trade_readiness - signal.candidate_penalty
            )
            signal.reasons.append(
                f"Testkandidat -{signal.candidate_penalty:g} Qualitätspunkte"
            )

        funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
        funding_hard = float(self.config.get("funding_hard_hourly_pct", 0.05))
        funding_against = (
            signal.funding_hourly_pct is not None
            and _directional(signal.funding_hourly_pct, direction) > 0
        )
        funding_magnitude = (
            _directional(signal.funding_hourly_pct or 0.0, direction)
            if funding_against
            else 0.0
        )
        if funding_magnitude > funding_watch:
            setup_factor = {
                "EARLY": 0.65,
                "TREND": 0.8,
                "REVERSAL": 0.25,
            }.get(selected.kind, 0.6)
            signal.trade_readiness -= _clamp(
                (funding_magnitude - funding_watch)
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
        tape_ok = signal.tape_quality >= float(self.config.get("minimum_tape_quality", 68))
        funding_block = (
            funding_magnitude > funding_hard
            and selected.kind not in {"REVERSAL"}
        )
        all_display_windows = len(display_good) == len(DISPLAY_WINDOWS)
        immediate_threshold = float(self.config.get("immediate_trade_readiness", 74))
        strong_threshold = float(self.config.get("strong_trade_readiness", 66))
        watch_threshold = float(self.config.get("watch_trade_readiness", 50))
        fresh_limit = (
            int(self.config.get("early_max_age_minutes", 2))
            if selected.kind == "EARLY" else 3
        )
        fresh_entry = selected.age_minutes is None or selected.age_minutes <= fresh_limit
        setup_minimum = float(
            self.config.get(
                {
                    "EARLY": "early_minimum_setup_score",
                    "TREND": "trend_minimum_setup_score",
                    "REVERSAL": "reversal_minimum_setup_score",
                }.get(selected.kind, ""),
                100.0,
            )
        )
        setup_is_precise = selected.score >= setup_minimum
        if selected.kind == "EARLY":
            effective_cost = signal.cost_pct if signal.cost_pct is not None else cost_limit
            expected_edge_required = max(
                float(self.config.get("early_minimum_expected_edge_pct", 0.10)),
                effective_cost * float(self.config.get("early_cost_edge_multiple", 3.2)),
            )
            setup_is_precise = (
                setup_is_precise
                and selected.volume_score >= float(self.config.get("early_minimum_volume_score", 50))
                and selected.expected_edge_pct >= expected_edge_required
                and selected.rejection_fraction >= float(self.config.get("early_min_efficiency", 0.52))
                and selected.recovery_fraction <= float(self.config.get("early_max_consumed_fraction", 0.66))
                and selected.confirmations >= int(self.config.get("early_min_volume_consistency", 2))
            )
        elif selected.kind == "TREND":
            effective_cost = (
                signal.cost_pct
                if signal.cost_pct is not None
                else cost_limit
            )
            expected_edge_required = max(
                float(
                    self.config.get(
                        "trend_minimum_expected_edge_pct",
                        0.10,
                    )
                ),
                effective_cost
                * float(self.config.get("trend_cost_edge_multiple", 3.0)),
            )
            setup_is_precise = (
                setup_is_precise
                and selected.event_strength >= float(
                    self.config.get("trend_minimum_context_strength", 84)
                )
                and selected.volume_score >= float(
                    self.config.get("trend_minimum_volume_score", 50)
                )
                and selected.expected_edge_pct >= expected_edge_required
            )
        elif selected.kind == "REVERSAL":
            recovery_limit = float(
                self.config.get(
                    "reversal_max_entry_recovery_fraction",
                    0.68,
                )
            )
            late_recovery_override = (
                selected.move_pct >= float(
                    self.config.get(
                        "reversal_late_recovery_override_move_pct",
                        1.0,
                    )
                )
                and selected.age_minutes is not None
                and selected.age_minutes <= 2
            )
            aligned_windows, opposed_windows = _directional_window_agreement(
                signal, direction
            )
            setup_is_precise = (
                setup_is_precise
                and selected.move_pct >= float(
                    self.config.get("reversal_immediate_min_move_pct", 0.75)
                )
                and selected.rejection_fraction >= float(
                    self.config.get("reversal_min_rejection_fraction", 0.30)
                )
                and selected.confirmations >= int(
                    self.config.get("reversal_immediate_min_confirmations", 2)
                )
                and aligned_windows >= int(
                    self.config.get("reversal_immediate_min_window_agreement", 2)
                )
                and opposed_windows <= int(
                    self.config.get("reversal_immediate_max_opposed_windows", 1)
                )
                and selected.age_minutes is not None
                and selected.age_minutes >= 1
                and selected.structural_reclaim
                and selected.relative_confirmed
                and not selected.new_extreme_after_event
                and (
                    selected.recovery_fraction <= recovery_limit
                    or late_recovery_override
                )
            )
        hard_block = (
            not executable
            or not meets_market_minimum
            or not enough_volume
            or not enough_oi
            or not tape_ok
            or funding_block
        )
        test_candidate_ready = (
            signal.candidate_tier != "test"
            or (
                signal.trade_readiness >= float(
                    self.config.get("test_candidate_minimum_readiness", 84.0)
                )
                and signal.confidence >= float(
                    self.config.get("test_candidate_minimum_confidence", 80.0)
                )
            )
        )

        if not executable or not meets_market_minimum:
            signal.state = "NO_TRADE"
            signal.reasons.append("Orderbuchkosten blockieren")
        elif not enough_volume or not enough_oi:
            signal.state = "NO_TRADE"
            signal.reasons.append("Liquidität/OI blockiert")
        elif not tape_ok:
            signal.state = "NO_TRADE"
            signal.reasons.append("Volumenverlauf zu lückenhaft/sprunghaft")
        elif funding_block:
            signal.state = "NO_TRADE"
            signal.reasons.append("Funding blockiert Richtung")
        elif (
            selected.phase == "ready"
            and all_display_windows
            and fresh_entry
            and setup_is_precise
            and not setup_conflict
            and test_candidate_ready
            and (selected.kind != "TREND" or bool(self.config.get("trend_immediate_enabled", False)))
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
        if signal.candidate_tier == "test" and not test_candidate_ready:
            signal.reasons.append("Testkandidat nur bei Spitzenqualität")
        if selected.exit_hint:
            signal.reasons.append("Setup abgelaufen/aussteigen prüfen")
        if funding_magnitude > funding_watch and not funding_block:
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
        btc_direction = _direction(btc_bias)
        breadth_symbols = {
            str(value).upper()
            for value in self.config.get("btc_breadth_symbols", [])
        }
        breadth_rows: list[tuple[float, float]] = []
        supporting = 0
        opposing = 0
        for item in signals:
            if (
                item.symbol == "BTC"
                or item.symbol not in breadth_symbols
                or item.state == "INVALID_DATA"
                or item.data_quality < 66.0
                or item.liquidity_score < 40.0
            ):
                continue
            bias = _market_bias(item)
            if bias is None:
                continue
            aligned = _directional(bias, btc_direction)
            weight = 0.65 + _clamp(item.liquidity_score, 0.0, 100.0) / 100.0 * 0.35
            breadth_rows.append((aligned, weight))
            supporting += aligned >= 8.0
            opposing += aligned <= -8.0

        btc_breadth: float | None = None
        if len(breadth_rows) >= 3:
            weighted_alignment = sum(
                _clamp(value, -60.0, 60.0) * weight
                for value, weight in breadth_rows
            ) / sum(weight for _, weight in breadth_rows)
            balance = (supporting - opposing) / len(breadth_rows)
            btc_breadth = _clamp(
                50.0 + weighted_alignment * 0.55 + balance * 22.0
            )
        btc.btc_context = btc_breadth

        if btc.state in {"BUY", "SELL"}:
            breadth_threshold = float(
                self.config.get(
                    {
                        "EARLY": "btc_early_immediate_breadth_score",
                        "TREND": "btc_trend_immediate_breadth_score",
                        "REVERSAL": "btc_reversal_immediate_breadth_score",
                    }.get(btc.selected_setup, ""),
                    100.0,
                )
            )
            if btc_breadth is None or btc_breadth < breadth_threshold:
                btc.state = (
                    "STRONG_LONG"
                    if btc.direction >= 0
                    else "STRONG_SHORT"
                )
                btc.reasons.append("Marktbreite verhindert BTC-Sofortfreigabe")

        for item in signals:
            if item.state == "INVALID_DATA":
                continue
            if item.symbol == "BTC":
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
            penalty = 7.0 if item.selected_setup == "REVERSAL" else (9.0 if item.selected_setup == "EARLY" else 12.0)
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

    def _apply_regime_context(
        self,
        signals: list[Signal],
        snapshots: Mapping[str, Mapping[str, Any]],
        daily_candles: Mapping[str, list[Mapping[str, Any]]],
        now: datetime,
    ) -> dict[str, Any]:
        regime_config = self.config.get("regime") or {}
        if not bool(regime_config.get("enabled", True)):
            return {}
        results = calculate_regimes(
            signals=signals,
            minute_snapshots=snapshots,
            daily_candles=daily_candles,
            now_ms=int(now.timestamp() * 1000),
            config=self.config,
        )
        immediate = float(self.config.get("immediate_trade_readiness", 76))
        strong = float(self.config.get("strong_trade_readiness", 64))
        watch = float(self.config.get("watch_trade_readiness", 56))
        early_block = float(regime_config.get("early_opposition_block_points", -6.5))
        warning_threshold = float(regime_config.get("warning_threshold_points", -4.0))

        def downgrade(item: Signal) -> None:
            direction_long = item.direction >= 0
            if item.state in {"BUY", "SELL"} and item.trade_readiness < immediate:
                item.state = "STRONG_LONG" if direction_long else "STRONG_SHORT"
            if item.state in {"STRONG_LONG", "STRONG_SHORT"} and item.trade_readiness < strong:
                item.state = "WATCH_LONG" if direction_long else "WATCH_SHORT"
            if item.state in {"WATCH_LONG", "WATCH_SHORT"} and item.trade_readiness < watch:
                item.state = "NO_TRADE"

        for item in signals:
            result = results.get(item.symbol)
            if result is None or not result.available:
                if result is not None and result.reason:
                    item.reasons.append(result.reason)
                continue
            item.regime_available = True
            item.regime_score = result.score
            item.regime_consistency = result.consistency
            item.regime_modifier = result.modifier
            item.return_7d = result.return_7d
            item.return_14d = result.return_14d
            item.return_30d = result.return_30d
            item.relative_7d = result.relative_7d
            item.relative_14d = result.relative_14d
            item.relative_30d = result.relative_30d
            item.btc_rebound_pct = result.btc_rebound_pct
            item.rebound_participation = result.rebound_participation
            item.relative_drift_60m = result.relative_drift_60m

            modifier = float(result.modifier)
            item.opportunity = _clamp(item.opportunity + modifier * 0.55)
            item.confidence = _clamp(item.confidence + modifier * 0.75)
            item.trade_readiness = _clamp(item.trade_readiness + modifier)

            # A W against a clearly stronger reference-market move remains W?
            # even if the local candle pattern reclaimed its first level. This
            # directly covers relief bounces that lag BTC/the broad pool and roll over.
            if item.selected_setup == "REVERSAL" and item.reversal.phase in {"ready", "strong"}:
                direction = 1 if item.direction >= 0 else -1
                drift_threshold = float(
                    regime_config.get("reversal_relative_drift_warning_pct", 0.30)
                )
                participation_threshold = float(
                    regime_config.get("reversal_min_rebound_participation", 0.45)
                )
                drift_opposition = (
                    result.relative_drift_60m is not None
                    and _directional(float(result.relative_drift_60m), direction) < -drift_threshold
                )
                participation_opposition = (
                    direction > 0
                    and result.rebound_participation is not None
                    and float(result.rebound_participation) < participation_threshold
                )
                item.reversal.relative_participation = result.rebound_participation
                item.reversal.relative_opposition = bool(
                    drift_opposition or participation_opposition
                )
                item.reversal.relative_confirmed = not item.reversal.relative_opposition
                if item.reversal.relative_opposition:
                    penalty = float(
                        regime_config.get("reversal_relative_opposition_penalty", 8.0)
                    )
                    item.trade_readiness = _clamp(item.trade_readiness - penalty)
                    item.confidence = _clamp(item.confidence - penalty * 0.55)
                    item.opportunity = _clamp(item.opportunity - penalty * 0.40)
                    if item.state in {"BUY", "SELL", "STRONG_LONG", "STRONG_SHORT"}:
                        item.state = "WATCH_LONG" if direction > 0 else "WATCH_SHORT"
                    item.reasons.append("W bleibt W?: relative Marktteilnahme zu schwach")

            if modifier <= warning_threshold:
                item.reasons.append(f"7/14/30D-Regime widerspricht ({modifier:+.1f})")
            elif modifier >= abs(warning_threshold):
                item.reasons.append(f"7/14/30D-Regime bestätigt ({modifier:+.1f})")

            # A strongly opposing multi-week regime may block a fresh E-entry,
            # but it never creates the opposite direction by itself.
            if (
                item.selected_setup == "EARLY"
                and modifier <= early_block
                and item.state in {"BUY", "SELL", "STRONG_LONG", "STRONG_SHORT"}
            ):
                item.state = "WATCH_LONG" if item.direction >= 0 else "WATCH_SHORT"
                item.reasons.append("frühes E durch Gegenregime blockiert")
            downgrade(item)
        return {symbol: result.to_dict() for symbol, result in results.items()}

    def _apply_extremity_context(
        self,
        signals: list[Signal],
        snapshots: Mapping[str, Mapping[str, Any]],
        daily_candles: Mapping[str, list[Mapping[str, Any]]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        hard_funding = float(self.config.get("funding_hard_hourly_pct", 0.05))

        def cap_immediate(item: Signal, *, to_watch: bool = False) -> None:
            if to_watch:
                if item.state in {"BUY", "STRONG_LONG"}:
                    item.state = "WATCH_LONG"
                elif item.state in {"SELL", "STRONG_SHORT"}:
                    item.state = "WATCH_SHORT"
            else:
                if item.state == "BUY":
                    item.state = "STRONG_LONG"
                elif item.state == "SELL":
                    item.state = "STRONG_SHORT"

        for item in signals:
            rows = list((snapshots.get(item.symbol) or {}).get("candles") or [])
            result = calculate_extremity(
                candles=rows,
                daily_candles=list(daily_candles.get(item.symbol) or []),
                current_price=item.price,
                current_timestamp_ms=item.candle_timestamp_ms,
                funding_hourly_pct=item.funding_hourly_pct,
                tape_quality=item.tape_quality,
                volume_confirmation=item.volume_confirmation,
                regime_score=item.regime_score if item.regime_available else None,
                funding_hard_hourly_pct=hard_funding,
            )
            item.extremity_available = result.available
            item.extremity_score = result.score
            item.extremity_confidence = result.confidence
            item.extremity_momentum = result.momentum
            item.extremity_vwap = result.vwap_deviation
            item.extremity_range = result.range_position
            item.extremity_funding = result.funding_crowding
            item.extremity_regime_adjustment = result.regime_adjustment
            item.extremity_intraday = result.intraday_score
            item.extremity_swing = result.swing_score
            item.extremity_swing_available = result.swing_available
            item.extremity_return_1d = result.return_1d
            item.extremity_return_3d = result.return_3d
            item.extremity_return_7d = result.return_7d

            if result.available and item.state not in {"INVALID_DATA", "NO_TRADE"}:
                direction = _direction(item.direction)
                chase = result.score * direction
                setup = _selected_setup(item)

                if item.selected_setup == "EARLY":
                    warning = float(self.config.get("early_chase_warning", 35.0))
                    block = float(self.config.get("early_chase_block", 50.0))
                    penalty = float(self.config.get("early_chase_penalty", 10.0))
                    window60 = item.windows.get(60)
                    aligned60 = bool(
                        window60 is not None
                        and window60.quality == "ok"
                        and window60.price_pct is not None
                        and _directional(window60.price_pct, direction)
                        >= max(0.03, item.noise_pct * 0.35)
                    )
                    exact_age = int(self.config.get("early_immediate_max_age_minutes", 1))
                    exact_used = float(self.config.get("early_immediate_max_consumed_fraction", 0.50))
                    has_broken = bool(setup.age_minutes is not None or setup.move_pct > 0)
                    stretch_chase = chase >= block or (chase >= warning and has_broken)
                    timing_chase = bool(
                        (setup.age_minutes is not None and setup.age_minutes > exact_age)
                        or setup.recovery_fraction > exact_used
                    )
                    item.chase_warning = bool(stretch_chase or timing_chase)
                    item.chase_blocked = bool(
                        chase >= block
                        or setup.recovery_fraction > float(self.config.get("early_max_consumed_fraction", 0.66))
                    )
                    if item.chase_warning:
                        applied = penalty if item.chase_blocked else penalty * 0.65
                        item.trade_readiness = _clamp(item.trade_readiness - applied)
                        item.confidence = _clamp(item.confidence - applied * 0.55)
                        item.opportunity = _clamp(item.opportunity - applied * 0.45)
                        item.state = "NO_TRADE"
                        item.reasons.append(
                            "E bereits gelaufen; WAIT statt Hinterherlaufen"
                            if has_broken else
                            "E zu stark überdehnt; WAIT"
                        )
                    elif item.state in {"BUY", "SELL"} and not aligned60 and chase >= warning * 0.75:
                        cap_immediate(item)
                        item.reasons.append("E NOW ohne 60m-Bestätigung begrenzt")

                elif item.selected_setup == "REVERSAL":
                    exhaustion = -result.score * direction
                    required = float(self.config.get("reversal_now_min_extremity", 18.0))
                    if item.state in {"BUY", "SELL"} and exhaustion < required:
                        cap_immediate(item)
                        item.reasons.append(
                            "W strukturell bestätigt, Extrempunkt aber nicht klar ausgereizt"
                        )
                else:
                    warning = float(self.config.get("extremity_chase_warning", 55.0))
                    block = float(self.config.get("extremity_chase_block", 72.0))
                    penalty = float(self.config.get("extremity_chase_penalty", 8.0))
                    if chase >= warning:
                        applied = penalty if chase >= block else penalty * 0.5
                        item.trade_readiness = _clamp(item.trade_readiness - applied)
                        item.confidence = _clamp(item.confidence - applied * 0.55)
                        item.opportunity = _clamp(item.opportunity - applied * 0.40)
                        cap_immediate(item, to_watch=chase >= block)
                        item.reasons.append(
                            f"Bewegung bereits gleichgerichtet überdehnt ({chase:.0f})"
                        )

            payload[item.symbol] = result.to_dict()
        return payload

    def _apply_event_context(
        self,
        signals: list[Signal],
        event_marks: Mapping[str, Any] | None,
        event_display_codes: Mapping[str, str] | None = None,
    ) -> None:
        marks = event_marks if isinstance(event_marks, Mapping) else {}
        display_codes_provided = isinstance(event_display_codes, Mapping)
        display_codes = event_display_codes if display_codes_provided else {}

        def field(mark: Any, name: str, default: Any = None) -> Any:
            if mark is None:
                return default
            if isinstance(mark, Mapping):
                return mark.get(name, default)
            return getattr(mark, name, default)

        btc_mark = marks.get("BTC")
        btc_kind = str(field(btc_mark, "kind", "") or "")
        for item in signals:
            own_mark = marks.get(item.symbol)
            global_mark = (
                btc_mark
                if item.symbol != "BTC" and btc_kind in GLOBAL_BTC_EVENT_KINDS
                else None
            )
            applicable = [mark for mark in (own_mark, global_mark) if mark is not None]
            if not applicable:
                item.event_display_code = str(display_codes.get(item.symbol, "") or "")
                continue

            # The coin-specific event owns the label/metadata. A confirmed BTC
            # macro event is additionally applied as market-wide risk without
            # duplicating its label on every altcoin.
            primary = own_mark or global_mark
            item.event_code = str(field(primary, "code", "") or "")
            display_default = item.event_code if own_mark is not None and not display_codes_provided else ""
            item.event_display_code = str(display_codes.get(item.symbol, display_default) or "")
            item.event_kind = str(field(primary, "kind", "") or "")
            item.event_title = str(field(primary, "title", "") or "")
            item.event_priority = max(
                _clamp(_f(field(mark, "priority", 0.0))) for mark in applicable
            )
            item.event_risk = max(
                _clamp(_f(field(mark, "risk", 0.0))) for mark in applicable
            )
            item.event_block_new = any(
                bool(field(mark, "block_new", False)) for mark in applicable
            )
            caps = [
                int(raw)
                for raw in (field(mark, "leverage_cap") for mark in applicable)
                if raw is not None
            ]
            item.event_leverage_cap = min(caps) if caps else None
            item.event_source_name = str(field(primary, "source_name", "") or "")
            item.event_source_url = str(field(primary, "source_url", "") or "")
            item.event_starts_at = field(primary, "starts_at")

            # Events never create a direction. They only reduce confidence or
            # block fresh risk close to a confirmed event.
            if item.event_risk >= 70:
                item.trade_readiness = _clamp(item.trade_readiness - 8.0)
                item.confidence = _clamp(item.confidence - 3.0)
            elif item.event_risk >= 45:
                item.trade_readiness = _clamp(item.trade_readiness - 4.0)
                item.confidence = _clamp(item.confidence - 2.0)

            active_network = any(
                str(field(mark, "kind", "") or "") == "NETWORK"
                and bool(field(mark, "active", False))
                for mark in applicable
            )
            reason_codes = []
            for mark in applicable:
                code = str(field(mark, "code", "") or "")
                if code and code not in reason_codes:
                    reason_codes.append(code)
            reason_code = "+".join(reason_codes) or "Ereignis"
            if active_network:
                item.state = "NO_TRADE"
                item.reasons.append(f"{reason_code} aktive Netzwerkstörung")
            elif item.event_block_new:
                if item.state == "BUY":
                    item.state = "STRONG_LONG"
                elif item.state == "SELL":
                    item.state = "STRONG_SHORT"
                elif item.state == "STRONG_LONG":
                    item.state = "WATCH_LONG"
                elif item.state == "STRONG_SHORT":
                    item.state = "WATCH_SHORT"
                item.reasons.append(f"{reason_code} blockiert neue Position kurzzeitig")
            else:
                item.reasons.append(f"kritisches Ereignis: {reason_code}")

    @staticmethod
    def _rank(signals: list[Signal]) -> list[Signal]:
        for item in signals:
            setup = _selected_setup(item)
            state_bonus = {
                "BUY": 16.0, "SELL": 16.0,
                "STRONG_LONG": 10.0, "STRONG_SHORT": 10.0,
                "WATCH_LONG": 4.0, "WATCH_SHORT": 4.0,
                "NO_TRADE": -8.0, "INVALID_DATA": -30.0,
            }.get(item.state, 0.0)
            late_penalty = 40.0 if setup.exit_hint or setup.phase in {"late", "invalidated"} else 0.0
            item.attention_score = _clamp(
                item.trade_readiness * 0.34
                + item.confidence * 0.18
                + item.opportunity * 0.20
                + setup.score * 0.18
                + item.tape_quality * 0.10
                + state_bonus
                - late_penalty
                - (float(5.0) if item.selected_setup == "TREND" else 0.0)
            )
        return sorted(
            signals,
            key=lambda item: (
                -STATE_TIER.get(item.state, 0),
                -item.attention_score,
                -item.trade_readiness,
                -item.confidence,
                item.alias,
            ),
        )

    def _include_extra_detail(self, signal: Signal, position: int) -> bool:
        setup = _selected_setup(signal)
        return bool(
            position <= int(self.config.get("maximum_detail_count", 4))
            and STATE_TIER.get(signal.state, 0) >= 2
            and setup.phase in {"ready", "strong", "forming"}
            and not setup.exit_hint
            and signal.attention_score >= float(self.config.get("detail_attention_threshold", 72))
        )

    @staticmethod
    def _summary_sort_key(item: Signal) -> tuple[float, float, float, float, str]:
        """Order the header from quiet to extreme, independently of direction.

        Positive extremity is overbought and negative extremity is oversold.  The
        magnitude therefore represents the user's shared meaning of
        "auffaellig" for both sides.  Readiness and attention only break ties;
        unavailable data stays at the quiet, left-hand edge.
        """
        available = bool(item.extremity_available and item.state != "INVALID_DATA")
        return (
            1.0 if available else 0.0,
            abs(float(item.extremity_score)) if available else 0.0,
            float(item.trade_readiness) if available else 0.0,
            float(item.attention_score) if available else 0.0,
            item.alias,
        )

    def _summary_items(self, signals: list[Signal]) -> list[Signal]:
        """Select the most relevant alts, sort ascending, then pin BTC right."""
        requested = {
            str(value).upper()
            for value in self.config.get("candidate_symbols", [])
        }
        anchor_symbol = str(self.config.get("summary_anchor_symbol", "BTC")).upper()
        count = int(self.config.get("summary_coin_count", len(requested)))
        anchor = next(
            (item for item in signals if item.symbol == anchor_symbol),
            None,
        )
        alt_slots = max(0, count - (1 if anchor is not None else 0))
        alternatives = [
            item
            for item in signals
            if item.symbol in requested and item.symbol != anchor_symbol
        ]
        selected = sorted(
            alternatives,
            key=self._summary_sort_key,
            reverse=True,
        )[:alt_slots]
        selected.sort(key=self._summary_sort_key)
        if anchor is not None:
            selected.append(anchor)
        return selected[:count]

    def _format(self, signals: list[Signal], now: datetime) -> str:
        ranked = self._rank(signals)
        summary = self._summary_items(signals)

        def summary_line(compact_clock: bool = False) -> str:
            tokens: list[str] = []
            for item in summary:
                event = item.event_display_code
                if compact_clock:
                    event = re.sub(r"@(\d{2}):\d{2}$", r"@\1", event)
                color = (
                    "⚫"
                    if item.state == "INVALID_DATA"
                    else extremity_color(item.extremity_score, item.extremity_available)
                )
                tokens.append(f"{item.alias}{color}{event}")
            return " ".join(tokens)

        header = summary_line(False)
        header_limit = int(
            self.config.get(
                "discord_header_max_codepoints",
                self.config.get("discord_max_codepoints_per_line", 58),
            )
        )
        if len(header) > header_limit:
            header = summary_line(True)
        if len(header) > header_limit:
            raise RuntimeError("Discord-Top-Zeilenlimit überschritten")
        lines = [header]

        maximum_details = int(self.config.get("maximum_detail_count", 4))
        threshold = float(self.config.get("detail_attention_threshold", 72))
        watch_threshold = float(self.config.get("detail_watch_attention_threshold", 82))
        btc = next((item for item in ranked if item.symbol == "BTC"), None)
        screamers = [
            item for item in ranked
            if item.symbol != "BTC"
            and self._include_extra_detail(item, 1)
            and (
                item.state not in {"WATCH_LONG", "WATCH_SHORT"}
                or item.attention_score >= watch_threshold
            )
        ]
        screamers.sort(
            key=lambda item: (
                -STATE_TIER.get(item.state, 0),
                -item.attention_score,
                -item.trade_readiness,
            )
        )
        # Strongest current signals get the first places; BTC remains the final
        # permanent reference and cannot be pushed out completely.
        details: list[Signal] = []
        reserve_btc = 1 if btc is not None else 0
        for item in screamers:
            if item.attention_score < threshold:
                continue
            if len(details) >= maximum_details - reserve_btc:
                break
            details.append(item)
        if btc is not None and btc not in details:
            details.append(btc)
        if not details and ranked:
            details = ranked[:1]
        details = details[:maximum_details]

        cost_limit = float(self.config.get("max_roundtrip_cost_pct", 0.10))
        funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
        tape_min = float(self.config.get("minimum_tape_quality", 68))
        regime_warning = float(
            (self.config.get("regime") or {}).get("warning_threshold_points", -4.0)
        )

        def invalid_code(item: Signal) -> str:
            reason = " ".join(item.reasons).lower()
            if "veraltet" in reason or "stale" in reason:
                return "STALE!"
            if "candle gap" in reason or "kerzenlücke" in reason:
                return "GAP!"
            if "/orderbookorders" in reason or "orderbuch" in reason:
                return "BOOK!"
            if "/candles" in reason or "kerzen" in reason:
                return "CND!"
            return "DATA!"

        for item in details:
            if item.state == "INVALID_DATA":
                line = f"⚫? {item.alias}00 {invalid_code(item)}"
                lines.append(line)
                continue

            windows = "".join(
                f"{minutes}{_window_color(item.windows.get(minutes, Window(minutes)))}"
                for minutes in DISPLAY_WINDOWS
            )
            warnings: list[str] = []
            if item.tape_quality < tape_min or item.volume_confirmation < 38:
                warnings.append("V!")
            if item.liquidity_score < 58:
                warnings.append("L!")
            if item.cost_pct is None or item.cost_pct > cost_limit:
                warnings.append("K!")
            if item.symbol != "BTC" and item.btc_context is not None and item.btc_context < 40:
                warnings.append("B!")
            if item.regime_available and item.regime_modifier <= regime_warning:
                warnings.append("R!")
            if item.selected_setup == "REVERSAL" and item.reversal.relative_opposition:
                warnings.append("RS!")
            if item.selected_setup == "EARLY" and item.chase_warning:
                warnings.append("CH!")
            if item.funding_hourly_pct is None:
                warnings.append("F!")
            else:
                against = _directional(
                    item.funding_hourly_pct,
                    _direction(item.direction),
                )
                if against > funding_watch:
                    warnings.append("F!")
            warning_text = "".join(warnings)
            setup_token = f"{_setup_code(item)}{_setup_age_code(item)}"
            extreme_token = extremity_code(
                item.extremity_score,
                item.extremity_available,
            )
            tail = " ".join(
                value for value in (
                    f"{item.alias}{round(item.trade_readiness):02d}",
                    _action_code(item),
                    setup_token,
                    extreme_token,
                    warning_text,
                ) if value
            )
            line = f"{_detail_head(item)} {windows} {tail}"
            lines.append(line)

        max_len = int(self.config.get("discord_max_codepoints_per_line", 58))
        if any(len(line) > max_len for line in lines[1:]):
            raise RuntimeError("Discord-Zeilenlimit überschritten")
        return "\n".join(lines)

    def run(
        self,
        *,
        event_marks: Mapping[str, Any] | None = None,
        event_display_codes: Mapping[str, str] | None = None,
        semantic_event_codes: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        self.generated_at = now
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
        snapshots: dict[str, dict[str, Any]] = {}
        daily_candles: dict[str, list[Mapping[str, Any]]] = {}
        workers = min(8, max(1, int(self.config.get("parallel_requests", 6))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._load_one, row): symbol
                for symbol, row in markets.items()
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    candles, book, daily = future.result()
                    snapshots[symbol] = {
                        "market": markets[symbol],
                        "candles": candles,
                        "book": book,
                    }
                    daily_candles[symbol] = daily
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
        regime_payload = self._apply_regime_context(signals, snapshots, daily_candles, now)
        extremity_payload = self._apply_extremity_context(signals, snapshots, daily_candles)
        self._apply_event_context(signals, event_marks, event_display_codes)
        self.last_signals = self._rank(signals)
        self.last_snapshots = snapshots
        report = self._format(signals, now)
        if semantic_event_codes is not None:
            previous_codes = {item.symbol: item.event_display_code for item in signals}
            for item in signals:
                item.event_display_code = str(semantic_event_codes.get(item.symbol, "") or "")
            semantic_report = self._format(signals, now)
            for item in signals:
                item.event_display_code = previous_codes.get(item.symbol, "")
        else:
            semantic_report = report
        minimum_lines = int(self.config.get("minimum_detail_count", 1)) + 1
        maximum_lines = int(self.config.get("maximum_detail_count", 4)) + 1
        if not minimum_lines <= len(report.splitlines()) <= maximum_lines:
            raise RuntimeError("Discord-Ausgabe hat unerwartete Zeilenzahl")
        payload = {
            "version": APP_VERSION,
            "package_revision": PACKAGE_REVISION,
            "generated_at": now.isoformat(),
            "report": report,
            "semantic_report": semantic_report,
            "signals": [asdict(item) for item in self.last_signals],
            "regime": regime_payload,
            "extremity": extremity_payload,
            "event_marks": {
                symbol: (asdict(mark) if hasattr(mark, "__dataclass_fields__") else dict(mark))
                for symbol, mark in (event_marks or {}).items()
            },
        }
        return report, payload

    def _load_one(
        self,
        market: Mapping[str, Any],
    ) -> tuple[list[Mapping[str, Any]], Mapping[str, Any], list[Mapping[str, Any]]]:
        market_id = int(market["market_id"])
        candle_count = int(self.config.get("candle_count", 360))
        daily_count = int((self.config.get("regime") or {}).get("daily_candle_count", 40))
        candles = self.client.candles(market_id, count=candle_count)
        book = self.client.book(market_id)
        try:
            daily = self.client.daily_candles(market_id, count=daily_count)
        except Exception:
            daily = []
        return candles, book, daily


# Package revision: v3.9.3-lighter-top-pool-r1
