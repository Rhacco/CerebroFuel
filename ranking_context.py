"""v3.3.0 cross-sectional bonuses and BTC comparison helpers.

The primary ranking remains the recent volume/price divergence.  This module only
provides bounded secondary context so market cap, seven-day volume and BTC
comparisons can never overpower the fresh trading-volume signal.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping

PURPLE = "🟣"
GREEN = "🟢"
BLUE = "🔵"
YELLOW = "🟡"
ORANGE = "🟠"
RED = "🔴"
WHITE = "⚪"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _delta_pct(value: Any) -> float:
    try:
        return (float(value) - 1.0) * 100.0
    except (TypeError, ValueError):
        return 0.0


def _percentile(sorted_values: list[float], value: float) -> float:
    """Mid-rank percentile with exact 0 for the smallest and 1 for the largest."""
    if not sorted_values or len(sorted_values) == 1:
        return 0.5
    below = sum(item < value for item in sorted_values)
    equal = sum(item == value for item in sorted_values)
    mid_rank = below + max(equal - 1, 0) / 2.0
    return _clamp(mid_rank / (len(sorted_values) - 1))


def _robust_z(values: Mapping[str, float | None]) -> dict[str, float | None]:
    cleaned = [float(value) for value in values.values() if value is not None and math.isfinite(float(value))]
    if not cleaned:
        return {key: None for key in values}
    center = statistics.median(cleaned)
    deviations = [abs(value - center) for value in cleaned]
    mad = statistics.median(deviations) if deviations else 0.0
    q1 = sorted(cleaned)[max(0, int((len(cleaned) - 1) * 0.25))]
    q3 = sorted(cleaned)[max(0, int((len(cleaned) - 1) * 0.75))]
    scale = max(mad * 1.4826, (q3 - q1) / 1.349, 1.0)
    return {
        key: None if value is None else max(-5.0, min(5.0, (float(value) - center) / scale))
        for key, value in values.items()
    }


def signed_color(value: float | None, *, light: float, clear: float, strong: float) -> str:
    if value is None or not math.isfinite(float(value)):
        return WHITE
    number = float(value)
    if number >= strong:
        return PURPLE
    if number >= clear:
        return GREEN
    if number >= light:
        return BLUE
    if number <= -strong:
        return RED
    if number <= -light:
        return ORANGE
    return YELLOW


def btc_performance_context(
    current: Mapping[str, Any],
    btc: Mapping[str, Any],
    *,
    is_reference: bool,
) -> tuple[float, str, float, str]:
    """Return 24h and 7d performance versus BTC.

    BTC itself receives an absolute 24h/7d context, because subtracting BTC from
    itself would otherwise make both reference circles permanently yellow.
    """
    delta = current.get("delta") or {}
    btc_delta = btc.get("delta") or {}
    day = _delta_pct(delta.get("day"))
    week = _delta_pct(delta.get("week"))
    if is_reference:
        day_relative = day
        week_relative = week
    else:
        day_relative = day - _delta_pct(btc_delta.get("day"))
        week_relative = week - _delta_pct(btc_delta.get("week"))
    day_color = signed_color(day_relative, light=0.45, clear=1.60, strong=4.50)
    week_color = signed_color(week_relative, light=1.20, clear=4.00, strong=10.00)
    return day_relative, day_color, week_relative, week_color


def seven_day_volume_context(
    raw_changes: Mapping[str, float | None],
) -> dict[str, dict[str, float | str | None]]:
    """Score the seven-day rolling-volume trend with positive-trend priority.

    Cross-sectional z-scores refine intensity, but never turn a genuinely
    positive raw trend orange merely because other coins rose even faster.
    """
    z_scores = _robust_z(raw_changes)
    result: dict[str, dict[str, float | str | None]] = {}
    for display, raw in raw_changes.items():
        z = z_scores.get(display)
        if raw is None or z is None:
            color = WHITE
            bonus = 0.0
        else:
            raw_value = float(raw)
            if raw_value >= 25.0 or z >= 1.80:
                color = PURPLE
            elif raw_value >= 8.0 or (raw_value > 0 and z >= 0.65):
                color = GREEN
            elif raw_value >= 1.0:
                color = BLUE
            elif raw_value <= -20.0 or z <= -1.80:
                color = RED
            elif raw_value <= -1.0:
                color = ORANGE
            else:
                color = YELLOW
            raw_component = _clamp(raw_value / 30.0) if raw_value > 0 else 0.0
            relative_component = _clamp((z + 0.15) / 2.50) if z > -0.15 else 0.0
            bonus = 14.0 * (0.58 * raw_component + 0.42 * relative_component)
        result[display] = {
            "pct": raw,
            "z": z,
            "color": color,
            "bonus": round(max(0.0, min(14.0, bonus)), 4),
        }
    return result


def small_cap_bonuses(
    rows: Mapping[str, Mapping[str, Any]],
    *,
    minimum_reliable_volume: float,
) -> dict[str, float]:
    """Return a bounded 0..10 small-cap bonus using log market-cap percentiles.

    The liquidity factor prevents a nearly inactive micro-cap from winning only
    because its market cap is tiny.
    """
    caps = sorted(
        math.log10(float(row.get("cap") or 0.0))
        for row in rows.values()
        if float(row.get("cap") or 0.0) > 0
    )
    bonuses: dict[str, float] = {}
    for display, row in rows.items():
        cap = float(row.get("cap") or 0.0)
        volume = float(row.get("volume") or 0.0)
        if cap <= 0 or not caps:
            bonuses[display] = 0.0
            continue
        cap_percentile = _percentile(caps, math.log10(cap))
        smallness = 1.0 - cap_percentile
        liquidity = _clamp(volume / max(minimum_reliable_volume * 4.0, 1.0), 0.20, 1.0)
        bonuses[display] = round(10.0 * smallness * liquidity, 4)
    return bonuses


def combined_priority(
    *,
    primary_gap_score: float,
    volume_7d_bonus: float,
    market_cap_bonus: float,
    volatility_score: float,
    recovery_score: float,
    quality: float = 1.0,
) -> float:
    """Volume-first final ranking score.

    Primary 30-minute divergence is unbounded by secondary context.  Secondary
    bonuses are capped: 14 volume-7d, 10 market-cap, 8 volatility, 12 recovery.
    """
    primary = max(0.0, min(100.0, float(primary_gap_score)))
    secondary = (
        max(0.0, min(14.0, float(volume_7d_bonus)))
        + max(0.0, min(10.0, float(market_cap_bonus)))
        + 8.0 * _clamp(float(volatility_score) / 100.0)
        + 12.0 * _clamp(float(recovery_score) / 100.0)
    )
    quality_factor = 0.72 + 0.28 * _clamp(quality)
    return round(primary * quality_factor + secondary, 4)
