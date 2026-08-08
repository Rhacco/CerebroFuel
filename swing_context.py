# r1
"""Live swing-speed, activity and BTC pinning context for CF v5.7.0.

The layer is deliberately direction-agnostic until extremity is applied:
- SPD measures how quickly price is moving now.
- ACT measures how actively the Lighter market is turning over now.
- PRE requires decisive extremity, speed/activity and continued pressure toward the extreme.
- two_sided_score checks whether meaningful movement exists in both directions.
- bounce_direction is the opposite side of a decisive OB/OS extremity.
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
    extension_score: float = 0.0
    extension_net_pct: float = 0.0
    extension_aligned_moves: int = 0
    extension_opposed_moves: int = 0
    pre_bounce_score: float = 0.0
    pre_bounce_direction: int = 0
    pre_bounce_eligible: bool = False
    two_sided_score: float = 0.0
    meaningful_up_moves: int = 0
    meaningful_down_moves: int = 0
    bounce_score: float = 0.0
    bounce_direction: int = 0
    bounce_eligible: bool = False
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
    exits: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_swing_metrics(
    *,
    candles: list[Mapping[str, Any]],
    open_interest_usd: float,
    tape_quality: float,
    extremity_score: float,
    extremity_available: bool,
    config: Mapping[str, Any],
) -> SwingResult:
    """Measure live speed/activity and split PRE from confirmed bounce.

    PRE deliberately means the market is still pressing toward the stretched
    side.  A confirmed bounce deliberately means enough meaningful movement
    exists on both sides.  A coin therefore cannot be PRE and confirmed at
    the same time.
    """
    speed_lookback = max(6, min(30, int(config.get("swing_speed_lookback_minutes", 12))))
    activity_lookback = max(3, min(15, int(config.get("swing_activity_lookback_minutes", 5))))
    two_sided_lookback = max(10, min(40, int(config.get("swing_two_sided_lookback_minutes", 20))))
    extension_lookback = max(3, min(10, int(config.get("pre_extension_lookback_minutes", 5))))
    needed = max(speed_lookback + 1, activity_lookback, two_sided_lookback + 1, extension_lookback + 1, 45)
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
    live_activity_score = _clamp(
        turnover_score * 0.50
        + pulse_score * 0.35
        + _clamp(float(tape_quality)) * 0.15
    )

    meaningful_threshold = max(
        0.01,
        baseline_median * float(config.get("swing_two_sided_noise_fraction", 0.80)),
    )

    # PRE pressure: the last few closed 1m moves must still lean toward the
    # current extremity (OB -> still pushing up, OS -> still pushing down).
    extremity_sign = 1 if extremity_score > 0 else (-1 if extremity_score < 0 else 0)
    extension_returns = _returns(candles[-(extension_lookback + 1):])
    aligned_extension = [value * extremity_sign for value in extension_returns] if extremity_sign else []
    extension_threshold = max(0.008, baseline_median * float(config.get("pre_extension_noise_fraction", 0.65)))
    extension_aligned = sum(value >= extension_threshold for value in aligned_extension)
    extension_opposed = sum(value <= -extension_threshold for value in aligned_extension)
    extension_meaningful = extension_aligned + extension_opposed
    extension_balance = (
        (extension_aligned - extension_opposed) / extension_meaningful
        if extension_meaningful else 0.0
    )
    extension_net_pct = sum(aligned_extension)
    expected_move = max(0.02, baseline_median * max(1, len(aligned_extension)))
    net_component = _clamp(extension_net_pct / expected_move, -1.0, 1.0)
    extension_score = _clamp(50.0 + extension_balance * 30.0 + net_component * 20.0)

    # Confirmed bounce needs genuine movement in both directions.
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

    abs_extremity = abs(float(extremity_score)) if extremity_available else 0.0
    bounce_direction = -1 if extremity_score > 0 else (1 if extremity_score < 0 else 0)
    bounce_score = _clamp(
        abs_extremity * 0.35
        + speed_score * 0.25
        + live_activity_score * 0.25
        + two_sided_score * 0.15
    )
    extremity_min = float(config.get("swing_extremity_min_abs", 30.0))
    speed_min = float(config.get("swing_speed_min_score", 45.0))
    activity_min = float(config.get("swing_activity_min_score", 45.0))
    two_sided_min = float(config.get("swing_two_sided_min_score", 20.0))
    two_sided_min_each = max(1, int(config.get("swing_two_sided_min_each_direction", 2)))
    bounce_min = float(config.get("swing_bounce_min_score", 52.0))
    eligible = bool(
        extremity_available
        and bounce_direction
        and abs_extremity >= extremity_min
        and recent_median >= floor_pct
        and speed_score >= speed_min
        and open_interest_usd > 0
        and recent_volume > 0
        and live_activity_score >= activity_min
        and up >= two_sided_min_each
        and down >= two_sided_min_each
        and two_sided_score >= two_sided_min
        and bounce_score >= bounce_min
    )

    pre_extremity_min = float(config.get("pre_extremity_min_abs", extremity_min))
    pre_speed_min = float(config.get("pre_speed_min_score", speed_min))
    pre_activity_min = float(config.get("pre_activity_min_score", activity_min))
    pre_extension_min = float(config.get("pre_extension_min_score", 52.0))
    pre_extension_min_aligned = max(1, int(config.get("pre_extension_min_aligned_moves", 2)))
    pre_impulse_threshold = max(floor_pct * 2.5, baseline_median * 3.5)
    extension_confirmed = bool(
        extension_aligned >= pre_extension_min_aligned
        or extension_net_pct >= pre_impulse_threshold
    )
    pre_score_min = float(config.get("pre_bounce_min_score", 52.0))
    pre_score = _clamp(
        abs_extremity * 0.40
        + speed_score * 0.25
        + live_activity_score * 0.25
        + extension_score * 0.10
    )
    pre_eligible = bool(
        extremity_available
        and bounce_direction
        and not eligible
        and abs_extremity >= pre_extremity_min
        and recent_median >= floor_pct
        and speed_score >= pre_speed_min
        and open_interest_usd > 0
        and recent_volume > 0
        and live_activity_score >= pre_activity_min
        and extension_score >= pre_extension_min
        and extension_net_pct > 0
        and extension_confirmed
        and pre_score >= pre_score_min
    )

    return SwingResult(
        available=True,
        speed_pct_per_min=round(recent_median, 6),
        speed_bps=round(recent_median * 100.0, 3),
        speed_score=round(speed_score, 4),
        speed_ratio=round(speed_ratio, 4),
        turnover_5m_pct=round(turnover_pct, 6),
        volume_pulse_ratio=round(pulse_ratio, 4),
        live_activity_score=round(live_activity_score, 4),
        extension_score=round(extension_score, 4),
        extension_net_pct=round(extension_net_pct, 6),
        extension_aligned_moves=extension_aligned,
        extension_opposed_moves=extension_opposed,
        pre_bounce_score=round(pre_score, 4),
        pre_bounce_direction=bounce_direction,
        pre_bounce_eligible=pre_eligible,
        two_sided_score=round(two_sided_score, 4),
        meaningful_up_moves=up,
        meaningful_down_moves=down,
        bounce_score=round(bounce_score, 4),
        bounce_direction=bounce_direction,
        bounce_eligible=eligible,
        reason=(
            "PRE = extremity + absolute/relative speed + live Lighter activity + continued extension; "
            "confirmed = extremity + speed/activity + two-sided movement"
        ),
    )

def calculate_pin(
    *,
    candles: list[Mapping[str, Any]],
    current_price: float,
    noise_pct: float,
    config: Mapping[str, Any],
) -> PinResult:
    if not bool(config.get("btc_pin_enabled", True)):
        return PinResult(reason="disabled")
    lookback = max(30, min(180, int(config.get("btc_pin_lookback_minutes", 60))))
    rows = candles[-lookback:]
    if len(rows) < max(30, lookback // 2) or not _contiguous(rows) or current_price <= 0:
        return PinResult(reason="insufficient BTC pin history")
    closes = [_f(row.get("c")) for row in rows]
    if min(closes, default=0.0) <= 0:
        return PinResult(reason="invalid BTC pin prices")

    step = max(100.0, float(config.get("btc_pin_level_step_usd", 1000.0)))
    nearest = round(current_price / step) * step
    candidates = [nearest - step, nearest, nearest + step]
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
        # PIN is a present-tense value: historical dwell around a level must not
        # win after BTC has already moved materially away from that level.
        if current_distance > band * current_band_multiple:
            continue
        inside = [abs(close - level) <= band for close in closes]
        dwell = sum(inside) / len(inside)
        distances = [abs(close - level) for close in closes]
        median_distance = statistics.median(distances)
        historical_proximity = _clamp((1.0 - median_distance / max(2.0 * band, 1e-9)) * 100.0)
        current_proximity = _clamp((1.0 - current_distance / max(band * current_band_multiple, 1e-9)) * 100.0)
        proximity = _clamp(historical_proximity * 0.60 + current_proximity * 0.40)

        exits = 0
        returns = 0
        for index in range(1, len(inside)):
            if not inside[index] and inside[index - 1]:
                exits += 1
                if any(inside[index + 1:index + 1 + return_minutes]):
                    returns += 1
        if exits:
            return_score = returns / exits * 100.0
        else:
            return_score = 100.0 if dwell >= 0.70 else (60.0 if dwell >= 0.50 else 40.0)
        dwell_score = dwell * 100.0
        score = _clamp(dwell_score * 0.45 + return_score * 0.25 + proximity * 0.30)
        result = PinResult(
            available=True,
            level=round(level, 8),
            score=round(score, 4),
            dwell_score=round(dwell_score, 4),
            return_score=round(return_score, 4),
            proximity_score=round(proximity, 4),
            current_proximity_score=round(current_proximity, 4),
            band_pct=round(band_pct, 6),
            observations=len(closes),
            exits=exits,
            reason="current-nearby level + dwell + return-to-level + proximity",
        )
        if best is None or result.score > best.score:
            best = result
    return best or PinResult(reason="no nearby round BTC level")
