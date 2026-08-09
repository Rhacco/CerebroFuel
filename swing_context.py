# r5
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
    band_usd: float = 0.0
    current_distance_usd: float = 0.0
    level_step_usd: float = 0.0
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

def _round_level_affinity(distance_usd: float, step_usd: float) -> float:
    """Continuous 0..100 affinity to a round level within half a level step.

    A price exactly on the level scores 100. At the midpoint between two round
    levels it reaches zero smoothly. This makes the geometry independent of the
    absolute BTC price and avoids the old percentage-based P00 cliff.
    """
    radius = max(step_usd * 0.5, 1e-9)
    distance = max(0.0, _f(distance_usd))
    x = _clamp(1.0 - distance / radius, 0.0, 1.0)
    # Smoothstep: continuous value and slope at both ends, so tiny price/noise
    # changes cannot create an artificial 00 -> high-score discontinuity.
    return _clamp((x * x * (3.0 - 2.0 * x)) * 100.0)


def calculate_pin(
    *,
    candles: list[Mapping[str, Any]],
    current_price: float,
    noise_pct: float,
    config: Mapping[str, Any],
) -> PinResult:
    """Measure persistent BTC attraction to the nearest round USD level.

    The score is deliberately normalized to the configured round-level spacing,
    not to BTC's absolute price. Current and historical proximity therefore mean
    the same thing at 65k, 115k or any other price. There is no hard current-price
    gate: affinity fades continuously to zero only at the exact midpoint between
    neighbouring round levels.

    P?? is reserved for unusable history. A valid P00 is possible only when the
    continuous pin score itself rounds to zero (normally around a midpoint with
    no meaningful attraction), never because a percentage threshold rejected the
    candidate before scoring.
    """
    if not bool(config.get("btc_pin_enabled", True)):
        return PinResult(reason="disabled")
    current_price = _f(current_price)
    if current_price <= 0:
        return PinResult(reason="invalid current BTC price")

    lookback = max(30, min(180, int(config.get("btc_pin_lookback_minutes", 60))))
    min_coverage = max(0.75, min(1.0, float(config.get("btc_pin_min_coverage", 0.90))))
    max_gap_minutes = max(1.0, min(5.0, float(config.get("btc_pin_max_gap_minutes", 3.0))))

    # Deduplicate and use an actual wall-clock window. Missing observations are
    # visible in coverage and may not silently stretch a nominal 60-minute PIN.
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
    lower = math.floor(current_price / step) * step
    upper = lower + step
    candidates = sorted({level for level in (lower, upper) if level > 0})

    # Return detection needs a compact inner band, but the final score does not
    # use this band as a current-price gate. The band itself is step-normalized;
    # noise may widen it modestly, capped well before the half-step midpoint.
    base_band_fraction = max(
        0.05,
        min(0.40, float(config.get("btc_pin_return_band_step_fraction", 0.18))),
    )
    max_band_fraction = max(
        base_band_fraction,
        min(0.45, float(config.get("btc_pin_return_band_max_step_fraction", 0.30))),
    )
    noise_multiple = max(0.0, float(config.get("btc_pin_noise_multiple", 3.0)))
    noise_usd = current_price * max(0.0, _f(noise_pct)) / 100.0
    return_band = min(
        step * max_band_fraction,
        max(step * base_band_fraction, noise_usd * noise_multiple),
    )
    # Hysteresis avoids counting one-bar boundary chatter as repeated exits.
    exit_band = min(step * 0.45, max(return_band * 1.35, return_band + step * 0.04))
    return_minutes = max(1, min(15, int(config.get("btc_pin_return_minutes", 5))))

    best: PinResult | None = None
    for level in candidates:
        current_distance = abs(current_price - level)
        current_affinity = _round_level_affinity(current_distance, step)
        historical_affinities = [
            _round_level_affinity(abs(close - level), step)
            for close in closes
        ]

        # Missing minutes count conservatively as zero dwell; proximity uses the
        # observed distribution and receives the separate coverage quality factor.
        dwell_score = _clamp(sum(historical_affinities) / float(lookback))
        historical_proximity = _clamp(statistics.median(historical_affinities))

        inside = [abs(close - level) <= return_band for close in closes]
        outside = [abs(close - level) >= exit_band for close in closes]
        exits = 0
        returns = 0
        horizon_ms = return_minutes * 60_000
        armed_inside = bool(inside[0]) if inside else False
        for index in range(1, len(points)):
            previous_stamp = points[index - 1][0]
            stamp = points[index][0]
            # Never infer an exit or return across a missing candle.
            if stamp - previous_stamp > 75_000:
                armed_inside = bool(inside[index])
                continue
            if inside[index]:
                armed_inside = True
                continue
            if armed_inside and outside[index]:
                # A late exit without a full return opportunity is not a failure.
                if latest_stamp - stamp < horizon_ms:
                    armed_inside = False
                    continue
                exits += 1
                deadline = stamp + horizon_ms
                # A return is evidence only if the observation chain from the
                # exit to the return is continuous. A missing 1m candle may hide
                # an unobserved crossing and therefore must not be bridged.
                previous_return_stamp = stamp
                returned = False
                for later in range(index + 1, len(points)):
                    later_stamp = points[later][0]
                    if later_stamp > deadline:
                        break
                    if later_stamp - previous_return_stamp > 75_000:
                        break
                    if inside[later]:
                        returned = True
                        break
                    previous_return_stamp = later_stamp
                if returned:
                    returns += 1
                armed_inside = False

        # If BTC never left the pin area, persistence itself is the relevant
        # evidence; do not invent either a perfect or failed return event.
        return_score = (returns / exits * 100.0) if exits else dwell_score

        raw_score = _clamp(
            dwell_score * 0.45
            + return_score * 0.20
            + historical_proximity * 0.20
            + current_affinity * 0.15
        )
        # Current affinity smoothly retires a historical pin as price moves toward
        # the midpoint. sqrt keeps a strong, recently-held level visible while BTC
        # is still reasonably near it, without allowing a stale pin at midpoint.
        current_factor = math.sqrt(max(0.0, current_affinity) / 100.0)
        score = _clamp(raw_score * current_factor * math.sqrt(coverage))

        result = PinResult(
            available=True,
            level=round(level, 8),
            score=round(score, 4),
            dwell_score=round(dwell_score, 4),
            return_score=round(return_score, 4),
            proximity_score=round(historical_proximity, 4),
            current_proximity_score=round(current_affinity, 4),
            band_pct=round(return_band / step * 100.0, 6),
            band_usd=round(return_band, 6),
            current_distance_usd=round(current_distance, 6),
            level_step_usd=round(step, 6),
            observations=observations,
            coverage_pct=round(coverage * 100.0, 4),
            largest_gap_minutes=round(largest_gap, 4),
            exits=exits,
            reason="step-normalized continuous proximity + dwell + return-to-level",
        )
        if best is None or result.score > best.score:
            best = result

    if best is not None:
        return best

    # With a valid positive price at least one neighbouring level must exist.
    # Keep this defensive path distinct from a valid P00 rather than hiding an
    # internal geometry error as 'no pin'.
    return PinResult(**diagnostics, reason="no valid BTC round-level candidate")

