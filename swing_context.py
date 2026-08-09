# r3
"""Live speed, activity, two-sided movement and BTC pinning context for CF v6.1.0.

The v5.5 signal state remains authoritative. This layer only adds timing context:
- SPD measures how quickly price is moving now.
- ACT measures how actively the Lighter market is turning over now.
- two_sided_score measures recent meaningful movement in both directions.
- PIN measures how persistently BTC is attracted to a nearby round level.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Mapping


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _pct(start: float, end: float) -> float:
    return 0.0 if start <= 0 else (end / start - 1.0) * 100.0


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    value = int(_f(row.get("t"), _f(row.get("timestamp"), _f(row.get("time")))))
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _contiguous(rows: list[Mapping[str, Any]]) -> bool:
    if len(rows) < 2:
        return bool(rows)
    stamps = [_timestamp_ms(row) for row in rows]
    if any(value <= 0 for value in stamps):
        return False
    # A closed Lighter 1m sequence must advance by roughly one minute.
    return all(45_000 <= right - left <= 75_000 for left, right in zip(stamps, stamps[1:]))


def _returns(rows: list[Mapping[str, Any]]) -> list[float]:
    result: list[float] = []
    for left_row, right_row in zip(rows, rows[1:]):
        left, right = _f(left_row.get("c")), _f(right_row.get("c"))
        left_stamp, right_stamp = _timestamp_ms(left_row), _timestamp_ms(right_row)
        if (
            left > 0
            and right > 0
            and left_stamp > 0
            and 45_000 <= right_stamp - left_stamp <= 75_000
        ):
            result.append(_pct(left, right))
    return result


def _baseline_returns(candles: list[Mapping[str, Any]], exclude: int, count: int = 120) -> list[float]:
    end = max(0, len(candles) - max(1, exclude))
    start = max(0, end - count)
    return _returns(candles[start:end])


def _baseline_volume(candles: list[Mapping[str, Any]], exclude: int, count: int = 120) -> float:
    end = max(0, len(candles) - max(1, exclude))
    start = max(0, end - count)
    values = [_f(row.get("V")) for row in candles[start:end] if _f(row.get("V")) > 0]
    return statistics.median(values) if values else 0.0


@dataclass(frozen=True)
class SwingResult:
    available: bool = False
    speed_pct_per_min: float = 0.0
    speed_bps: float = 0.0
    speed_score: float = 0.0
    speed_ratio: float = 0.0
    turnover_5m_pct: float = 0.0
    volume_pulse_ratio: float = 0.0
    live_activity_score: float = 0.0
    two_sided_score: float = 0.0
    meaningful_up_moves: int = 0
    meaningful_down_moves: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PinResult:
    available: bool = False
    level: float = 0.0
    score: float = 0.0
    dwell_score: float = 0.0
    return_score: float = 0.0
    proximity_score: float = 0.0
    current_proximity_score: float = 0.0
    band_pct: float = 0.0
    observations: int = 0
    coverage_pct: float = 0.0
    largest_gap_minutes: float = 0.0
    exits: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_swing_metrics(
    *,
    candles: list[Mapping[str, Any]],
    open_interest_usd: float,
    tape_quality: float,
    config: Mapping[str, Any],
) -> SwingResult:
    """Measure live speed/activity and diagnostic two-sided movement.

    The result does not create or promote a trading state. v5.5-style
    NEAR/TRY/NOW remains authoritative; these metrics only confirm, rank and
    size those states.
    """
    speed_lookback = max(6, min(30, int(config.get("swing_speed_lookback_minutes", 12))))
    activity_lookback = max(3, min(15, int(config.get("swing_activity_lookback_minutes", 5))))
    two_sided_lookback = max(10, min(40, int(config.get("swing_two_sided_lookback_minutes", 20))))
    needed = max(speed_lookback + 1, activity_lookback, two_sided_lookback + 1, 45)
    rows = candles[-needed:]
    if len(rows) < needed or not _contiguous(rows):
        return SwingResult(reason="insufficient contiguous 1m candles")

    recent_speed_rows = candles[-(speed_lookback + 1):]
    recent_returns = [abs(value) for value in _returns(recent_speed_rows)]
    baseline = [abs(value) for value in _baseline_returns(candles, speed_lookback, 120)]
    if not recent_returns or len(baseline) < 45:
        return SwingResult(reason="missing/fragmented speed baseline")

    recent_median = statistics.median(recent_returns)
    baseline_median = max(0.004, statistics.median(baseline))
    speed_ratio = recent_median / baseline_median
    relative_score = _clamp(50.0 + 32.0 * math.log2(max(speed_ratio, 0.125)))
    floor_pct = max(0.005, float(config.get("swing_speed_absolute_floor_pct", 0.025)))
    strong_pct = max(floor_pct + 0.01, float(config.get("swing_speed_strong_pct", 0.16)))
    absolute_score = _clamp((recent_median - floor_pct) / (strong_pct - floor_pct) * 100.0)
    speed_score = _clamp(relative_score * 0.55 + absolute_score * 0.45)

    recent_activity_rows = candles[-activity_lookback:]
    recent_volume = sum(_f(row.get("V")) for row in recent_activity_rows)
    turnover_pct = (
        recent_volume / open_interest_usd * 100.0
        if open_interest_usd > 0 and recent_volume > 0
        else 0.0
    )
    baseline_volume = _baseline_volume(candles, activity_lookback, 120)
    current_average = recent_volume / activity_lookback if activity_lookback else 0.0
    pulse_ratio = current_average / baseline_volume if baseline_volume > 0 else 0.0
    turnover_reference = max(
        0.01,
        float(config.get("swing_activity_turnover_reference_5m_pct", 0.30)),
    )
    turnover_score = (
        _clamp(50.0 + 25.0 * math.log10(max(turnover_pct, 1e-6) / turnover_reference))
        if turnover_pct > 0
        else 0.0
    )
    pulse_score = (
        _clamp(50.0 + 30.0 * math.log2(max(pulse_ratio, 0.125)))
        if pulse_ratio > 0
        else 0.0
    )
    # Activity is explicitly a market-turnover measure relative to OI.
    # Without a valid OI denominator we keep speed/two-sided context usable,
    # but must not manufacture a seemingly valid activity score from pulse/tape.
    live_activity_score = (
        _clamp(
            turnover_score * 0.50
            + pulse_score * 0.35
            + _clamp(float(tape_quality)) * 0.15
        )
        if open_interest_usd > 0
        else 0.0
    )

    meaningful_threshold = max(
        0.01,
        baseline_median * float(config.get("swing_two_sided_noise_fraction", 0.80)),
    )
    two_rows = candles[-(two_sided_lookback + 1):]
    directional = _returns(two_rows)
    up = sum(value >= meaningful_threshold for value in directional)
    down = sum(value <= -meaningful_threshold for value in directional)
    meaningful = up + down
    if up > 0 and down > 0 and directional:
        balance = 2.0 * min(up, down) / meaningful
        coverage = meaningful / len(directional)
        two_sided_score = _clamp(100.0 * math.sqrt(max(0.0, balance * coverage)))
    else:
        two_sided_score = 0.0

    return SwingResult(
        available=True,
        speed_pct_per_min=round(recent_median, 6),
        speed_bps=round(recent_median * 100.0, 3),
        speed_score=round(speed_score, 4),
        speed_ratio=round(speed_ratio, 4),
        turnover_5m_pct=round(turnover_pct, 6),
        volume_pulse_ratio=round(pulse_ratio, 4),
        live_activity_score=round(live_activity_score, 4),
        two_sided_score=round(two_sided_score, 4),
        meaningful_up_moves=up,
        meaningful_down_moves=down,
        reason=(
            "live speed/activity plus two-sided movement context"
            if open_interest_usd > 0
            else "live speed/two-sided context; activity unavailable without OI"
        ),
    )

def calculate_pin(
    *,
    candles: list[Mapping[str, Any]],
    current_price: float,
    noise_pct: float,
    config: Mapping[str, Any],
) -> PinResult:
    """Measure whether BTC is currently attracted to a nearby round USD level.

    PIN is deliberately tolerant of a small number of missing 1m bars. Coverage
    and the largest actual timestamp gap are checked explicitly so a single
    missing candle does not turn a healthy feed into an unavailable PIN marker,
    while fragmented history still cannot manufacture a score.
    """
    if not bool(config.get("btc_pin_enabled", True)):
        return PinResult(reason="disabled")
    current_price = _f(current_price)
    if current_price <= 0:
        return PinResult(reason="invalid current BTC price")

    lookback = max(30, min(180, int(config.get("btc_pin_lookback_minutes", 60))))
    min_coverage = max(0.75, min(1.0, float(config.get("btc_pin_min_coverage", 0.90))))
    max_gap_minutes = max(1.0, min(5.0, float(config.get("btc_pin_max_gap_minutes", 3.0))))

    # Deduplicate/sort valid closes and keep an actual wall-clock lookback. Using
    # the last N rows would silently stretch a 60m PIN window when candles are
    # missing, which is exactly the situation this metric must diagnose cleanly.
    by_stamp: dict[int, float] = {}
    for row in candles:
        stamp = _timestamp_ms(row)
        close = _f(row.get("c"))
        if stamp > 0 and close > 0:
            by_stamp[stamp] = close
    if not by_stamp:
        return PinResult(reason="missing BTC pin history")

    latest_stamp = max(by_stamp)
    first_stamp = latest_stamp - (lookback - 1) * 60_000
    points = [(stamp, by_stamp[stamp]) for stamp in sorted(by_stamp) if stamp >= first_stamp]
    observations = len(points)
    coverage = observations / float(lookback)
    gaps = [
        (right[0] - left[0]) / 60_000.0
        for left, right in zip(points, points[1:])
        if right[0] > left[0]
    ]
    largest_gap = max(gaps, default=1.0 if observations else 0.0)
    diagnostics = {
        "observations": observations,
        "coverage_pct": round(coverage * 100.0, 4),
        "largest_gap_minutes": round(largest_gap, 4),
    }
    minimum_observations = max(24, int(math.ceil(lookback * min_coverage)))
    if observations < minimum_observations or coverage < min_coverage:
        return PinResult(**diagnostics, reason="insufficient BTC pin coverage")
    if any(gap < 0.75 for gap in gaps):
        return PinResult(**diagnostics, reason="irregular BTC pin timestamps")
    if largest_gap > max_gap_minutes + 1e-9:
        return PinResult(**diagnostics, reason="BTC pin history too fragmented")

    closes = [close for _, close in points]
    step = max(100.0, float(config.get("btc_pin_level_step_usd", 1000.0)))
    nearest = round(current_price / step) * step
    candidates = sorted({nearest - step, nearest, nearest + step})
    band_pct_cfg = max(0.03, float(config.get("btc_pin_band_pct", 0.15)))
    noise_band_pct = max(0.0, float(noise_pct)) * float(config.get("btc_pin_noise_multiple", 3.0))
    band_pct = max(band_pct_cfg, noise_band_pct)
    return_minutes = max(1, min(15, int(config.get("btc_pin_return_minutes", 5))))
    current_band_multiple = max(1.0, min(4.0, float(config.get("btc_pin_current_band_multiple", 2.0))))

    best: PinResult | None = None
    for level in candidates:
        if level <= 0:
            continue
        band = level * band_pct / 100.0
        current_distance = abs(current_price - level)
        # Historical attraction only matters while BTC is still close enough to
        # the same level now. A past pin must not survive a genuine move away.
        if current_distance > band * current_band_multiple:
            continue

        inside = [abs(close - level) <= band for close in closes]
        # Missing observations count conservatively against dwell instead of
        # inflating it. This still leaves a one-candle API hole almost neutral.
        dwell = sum(inside) / float(lookback)
        distances = [abs(close - level) for close in closes]
        median_distance = statistics.median(distances)
        historical_proximity = _clamp(
            (1.0 - median_distance / max(2.0 * band, 1e-9)) * 100.0
        )
        current_proximity = _clamp(
            (1.0 - current_distance / max(band * current_band_multiple, 1e-9)) * 100.0
        )
        proximity = _clamp(
            historical_proximity * 0.60 + current_proximity * 0.40
        )

        exits = 0
        returns = 0
        horizon_ms = return_minutes * 60_000
        for index in range(1, len(points)):
            previous_stamp = points[index - 1][0]
            stamp = points[index][0]
            # Do not infer an exit across a missing candle.
            if stamp - previous_stamp > 75_000:
                continue
            if not inside[index] and inside[index - 1]:
                # An exit too close to the end has not yet had its full return
                # opportunity, so it is not counted as a failed return.
                if latest_stamp - stamp < horizon_ms:
                    continue
                exits += 1
                deadline = stamp + horizon_ms
                if any(
                    inside[later]
                    for later in range(index + 1, len(points))
                    if points[later][0] <= deadline
                ):
                    returns += 1
        if exits:
            return_score = returns / exits * 100.0
        elif dwell >= 0.70:
            return_score = 100.0
        elif dwell >= 0.50:
            return_score = 70.0
        elif dwell >= 0.30:
            return_score = 40.0
        elif dwell >= 0.15:
            return_score = 15.0
        else:
            return_score = 0.0

        dwell_score = _clamp(dwell * 100.0)
        raw_score = _clamp(dwell_score * 0.45 + return_score * 0.25 + proximity * 0.30)
        # Coverage is already represented in dwell. A mild square-root quality
        # factor prevents sparse-but-allowed history from looking stronger than
        # a complete window without overreacting to one isolated missing bar.
        score = _clamp(raw_score * math.sqrt(coverage))
        result = PinResult(
            available=True,
            level=round(level, 8),
            score=round(score, 4),
            dwell_score=round(dwell_score, 4),
            return_score=round(return_score, 4),
            proximity_score=round(proximity, 4),
            current_proximity_score=round(current_proximity, 4),
            band_pct=round(band_pct, 6),
            observations=observations,
            coverage_pct=round(coverage * 100.0, 4),
            largest_gap_minutes=round(largest_gap, 4),
            exits=exits,
            reason="current-nearby level + dwell + return-to-level + proximity",
        )
        if best is None or result.score > best.score:
            best = result

    if best is not None:
        return best

    # No nearby round level is a valid market finding, not a data error. This is
    # the only normal case that should legitimately display P00.
    return PinResult(
        available=True,
        level=round(nearest, 8) if nearest > 0 else 0.0,
        score=0.0,
        band_pct=round(band_pct, 6),
        observations=observations,
        coverage_pct=round(coverage * 100.0, 4),
        largest_gap_minutes=round(largest_gap, 4),
        reason="current BTC price not near a round level",
    )

