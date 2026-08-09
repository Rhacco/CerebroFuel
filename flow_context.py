# r4
"""Multi-horizon runability/jumpiness context for CF v6.1.0.

Visible output:
- ER±xx: signed path-efficiency regime. Positive = smooth/runable, negative =
  jumpy/two-sided, near zero = mixed. The sign is NOT long/short direction.
- AGEyy: how long the same structural regime has persisted across progressively
  longer horizons, from hours through days/weeks.

The v5.5-derived signal state remains authoritative. This module does not create
trade directions; it only describes the current path structure.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

CURRENT_HORIZONS_MINUTES = (30, 120, 300)
CURRENT_WEIGHTS = {30: 0.50, 120: 0.30, 300: 0.20}
# AGE is intentionally ordinal, not fake clock precision. Each point means that
# the same structural regime survived another materially longer horizon.
AGE_STEPS = (
    ("2h", "minute", 120, 12),
    ("5h", "minute", 300, 12),
    ("2d", "daily", 2, 14),
    ("3d", "daily", 3, 16),
    ("7d", "daily", 7, 16),
    ("14d", "daily", 14, 15),
    ("30d", "daily", 30, 14),
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    value = int(_f(row.get("t"), _f(row.get("timestamp"), _f(row.get("time")))))
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _clean_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_time: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        stamp = _timestamp_ms(row)
        close = _f(row.get("c"))
        if stamp > 0 and close > 0:
            by_time[stamp] = row
    return [by_time[key] for key in sorted(by_time)]


def _minute_contiguous(rows: Sequence[Mapping[str, Any]]) -> bool:
    clean = list(rows)
    if len(clean) < 2:
        return bool(clean)
    stamps = [_timestamp_ms(row) for row in clean]
    if any(stamp <= 0 for stamp in stamps):
        return False
    return all(45_000 <= right - left <= 75_000 for left, right in zip(stamps, stamps[1:]))


def _daily_contiguous(rows: Sequence[Mapping[str, Any]]) -> bool:
    clean = list(rows)
    if len(clean) < 2:
        return bool(clean)
    stamps = [_timestamp_ms(row) for row in clean]
    if any(stamp <= 0 for stamp in stamps):
        return False
    return all(20 * 3_600_000 <= right - left <= 28 * 3_600_000 for left, right in zip(stamps, stamps[1:]))


def _daily_range_pct(row: Mapping[str, Any]) -> float:
    high = _f(row.get("h"))
    low = _f(row.get("l"))
    close = _f(row.get("c"))
    if high > 0 and low > 0 and high >= low and close > 0:
        return (high - low) / close * 100.0
    return 0.0


def _typical_daily_range_pct(daily_rows: Sequence[Mapping[str, Any]]) -> float | None:
    clean = _clean_rows(daily_rows)
    values = [_daily_range_pct(row) for row in clean[-20:] if _daily_range_pct(row) > 0]
    if len(values) >= 5:
        return max(0.50, min(20.0, statistics.median(values)))

    closes = [_f(row.get("c")) for row in clean[-21:]]
    returns: list[float] = []
    for left, right in zip(closes, closes[1:]):
        if left > 0 and right > 0:
            returns.append(abs((right / left - 1.0) * 100.0))
    if len(returns) >= 5:
        return max(0.50, min(20.0, statistics.median(returns) * 1.75))
    # Do not manufacture an ER normalisation from a fixed assumed volatility.
    # Without an empirical daily baseline, current flow stays unavailable.
    return None


@dataclass(frozen=True)
class PathShape:
    available: bool = False
    score: float = 0.0
    efficiency_ratio: float = 0.0
    jump_strength: float = 0.0
    run_strength: float = 0.0
    direction: int = 0
    range_pct: float = 0.0
    expected_range_pct: float = 0.0
    balance: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FlowResult:
    available: bool = False
    score: float = 0.0
    raw_efficiency_ratio: float = 0.0
    age_available: bool = False
    age_score: float = 0.0
    regime: str = "UNKNOWN"
    direction: int = 0
    horizons: dict[str, dict[str, Any]] | None = None
    age_matches: tuple[str, ...] = ()
    age_consistency: dict[str, float] | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["age_matches"] = list(self.age_matches)
        return payload


def _shape_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_range_pct: float,
    contiguous: bool,
    scale: float,
) -> PathShape:
    clean = _clean_rows(rows)
    if len(clean) < 3:
        return PathShape(reason="insufficient path history")
    if contiguous and not _minute_contiguous(clean):
        return PathShape(reason="fragmented minute path")
    if not contiguous and not _daily_contiguous(clean):
        return PathShape(reason="fragmented daily path")

    prices = [_f(row.get("c")) for row in clean]
    if min(prices, default=0.0) <= 0:
        return PathShape(reason="invalid path prices")

    deltas = [right - left for left, right in zip(prices, prices[1:])]
    path = sum(abs(value) for value in deltas)
    if path <= 1e-12:
        return PathShape(
            available=True,
            score=0.0,
            efficiency_ratio=0.0,
            direction=0,
            range_pct=0.0,
            expected_range_pct=round(max(expected_range_pct, 1e-9), 6),
            reason="flat path",
        )

    net_delta = prices[-1] - prices[0]
    direction = 1 if net_delta > 0 else -1 if net_delta < 0 else 0
    # Kaufman-style path efficiency: displacement divided by total travelled path.
    efficiency = _clamp(abs(net_delta) / path, 0.0, 1.0)

    up_path = sum(value for value in deltas if value > 0)
    down_path = sum(-value for value in deltas if value < 0)
    two_way_path = up_path + down_path
    balance = 2.0 * min(up_path, down_path) / two_way_path if two_way_path > 0 else 0.0
    persistence = max(up_path, down_path) / two_way_path if two_way_path > 0 else 0.5

    base = prices[0]
    highs = [max(_f(row.get("h"), close), close) for row, close in zip(clean, prices)]
    lows = [min(_f(row.get("l"), close), close) for row, close in zip(clean, prices)]
    range_pct = (max(highs) - min(lows)) / base * 100.0 if base > 0 else 0.0
    expected = max(0.02, expected_range_pct)
    # Quiet sideways action must not be classified like violent whipsaw. The
    # negative side is therefore gated by realised range relative to a robust
    # daily-range expectation.
    range_factor = _clamp(range_pct / expected, 0.0, 1.0)

    # Both sides require meaningful realised movement. A nearly motionless but
    # perfectly straight path must not look "highly runable" merely because its
    # mathematical efficiency is close to one.
    run_strength = efficiency * (0.70 + 0.30 * persistence) * range_factor
    jump_strength = (1.0 - efficiency) * balance * range_factor
    bounded_scale = max(0.5, scale)
    normaliser = max(math.tanh(bounded_scale), 1e-9)
    signed = (
        math.tanh(bounded_scale * (run_strength - jump_strength))
        / normaliser
        * 99.0
    )

    return PathShape(
        available=True,
        score=round(_clamp(signed, -99.0, 99.0), 4),
        efficiency_ratio=round(efficiency, 6),
        jump_strength=round(jump_strength, 6),
        run_strength=round(run_strength, 6),
        direction=direction,
        range_pct=round(range_pct, 6),
        expected_range_pct=round(expected, 6),
        balance=round(balance, 6),
        reason="path efficiency with range-gated two-sided jumpiness",
    )


def _minute_shape(
    rows: Sequence[Mapping[str, Any]],
    minutes: int,
    *,
    typical_daily_range_pct: float,
    scale: float,
) -> PathShape:
    clean = _clean_rows(rows)
    needed = minutes + 1
    if len(clean) < needed:
        return PathShape(reason=f"{len(clean)}/{needed} minute rows")
    window = clean[-needed:]
    expected = typical_daily_range_pct * math.sqrt(minutes / 1440.0)
    return _shape_from_rows(window, expected_range_pct=expected, contiguous=True, scale=scale)


def _daily_shape(
    rows: Sequence[Mapping[str, Any]],
    days: int,
    *,
    typical_daily_range_pct: float,
    scale: float,
) -> PathShape:
    clean = _clean_rows(rows)
    needed = days + 1
    if len(clean) < needed:
        return PathShape(reason=f"{len(clean)}/{needed} daily rows")
    window = clean[-needed:]
    expected = typical_daily_range_pct * math.sqrt(float(days))
    return _shape_from_rows(window, expected_range_pct=expected, contiguous=False, scale=scale)


def _regime(score: float, run_threshold: float, jump_threshold: float) -> str:
    if score >= run_threshold:
        return "RUN"
    if score <= jump_threshold:
        return "JUMP"
    return "MIXED"


def _age_match(
    *,
    current_regime: str,
    current_direction: int,
    shape: PathShape,
    run_match_threshold: float,
    jump_match_threshold: float,
    jump_strength_min: float,
    jump_efficiency_max: float,
    mixed_band: float,
) -> bool:
    if not shape.available:
        return False
    if current_regime == "RUN":
        return shape.score >= run_match_threshold and current_direction != 0 and shape.direction == current_direction
    if current_regime == "JUMP":
        # Odd-length zig-zag windows can have a non-zero displacement even when
        # the path is unmistakably two-sided. Accept either a clearly negative
        # signed score or the underlying range-gated jump structure itself.
        return shape.score <= jump_match_threshold or (
            shape.jump_strength >= jump_strength_min
            and shape.efficiency_ratio <= jump_efficiency_max
            and shape.balance >= 0.50
        )
    return abs(shape.score) <= mixed_band


def _daily_rolling_consistency(
    rows: Sequence[Mapping[str, Any]],
    *,
    days: int,
    current_regime: str,
    current_direction: int,
    typical_daily_range_pct: float,
    scale: float,
    run_match_threshold: float,
    jump_match_threshold: float,
    jump_strength_min: float,
    jump_efficiency_max: float,
    mixed_band: float,
) -> float:
    """How consistently the long horizon has exhibited the current regime.

    The aggregate 14/30d path alone can hide a large regime flip in the middle.
    For horizons >=7d, require the recent rolling subpaths to agree as well.
    """
    if days < 7:
        return 1.0
    clean = _clean_rows(rows)
    needed = days + 1
    if len(clean) < needed:
        return 0.0
    window = clean[-needed:]
    segment_days = 3 if days <= 7 else 5 if days <= 14 else 7
    checks: list[bool] = []
    for end in range(segment_days, len(window)):
        segment = window[end - segment_days:end + 1]
        shape = _shape_from_rows(
            segment,
            expected_range_pct=typical_daily_range_pct * math.sqrt(float(segment_days)),
            contiguous=False,
            scale=scale,
        )
        checks.append(
            _age_match(
                current_regime=current_regime,
                current_direction=current_direction,
                shape=shape,
                run_match_threshold=run_match_threshold,
                jump_match_threshold=jump_match_threshold,
                jump_strength_min=jump_strength_min,
                jump_efficiency_max=jump_efficiency_max,
                mixed_band=mixed_band,
            )
        )
    if not checks:
        return 0.0
    # Recent structure matters more than old structure while still requiring
    # broad persistence across the full lookback.
    weights = list(range(1, len(checks) + 1))
    return sum(weight for weight, ok in zip(weights, checks) if ok) / sum(weights)


def calculate_flow_metrics(
    *,
    minute_candles: Sequence[Mapping[str, Any]],
    daily_candles: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> FlowResult:
    """Return current runability/jumpiness and cross-horizon regime age."""
    if not bool(config.get("flow_enabled", True)):
        return FlowResult(reason="disabled")

    minute_rows = _clean_rows(minute_candles)
    daily_rows = _clean_rows(daily_candles)
    typical_daily_range = _typical_daily_range_pct(daily_rows)
    if typical_daily_range is None:
        return FlowResult(reason="insufficient empirical daily range baseline")
    scale = float(config.get("flow_score_scale", 2.4))

    shapes: dict[str, PathShape] = {}
    weighted_score = 0.0
    weighted_er = 0.0
    total_weight = 0.0
    for minutes in CURRENT_HORIZONS_MINUTES:
        shape = _minute_shape(
            minute_rows,
            minutes,
            typical_daily_range_pct=typical_daily_range,
            scale=scale,
        )
        shapes[f"{minutes}m"] = shape
        if not shape.available:
            continue
        weight = CURRENT_WEIGHTS[minutes]
        weighted_score += shape.score * weight
        weighted_er += shape.efficiency_ratio * weight
        total_weight += weight

    # Never silently change ER meaning because one horizon is missing.
    if total_weight < 0.999:
        return FlowResult(
            horizons={key: value.to_dict() for key, value in shapes.items()},
            reason="incomplete 30m/2h/5h path history",
        )

    current_score = _clamp(weighted_score / total_weight, -99.0, 99.0)
    current_er = _clamp(weighted_er / total_weight, 0.0, 1.0)
    run_threshold = float(config.get("flow_run_threshold", 20.0))
    jump_threshold = float(config.get("flow_jump_threshold", -20.0))
    regime = _regime(current_score, run_threshold, jump_threshold)

    direction = shapes["120m"].direction
    if direction == 0:
        direction = shapes["300m"].direction or shapes["30m"].direction

    age_score = 0
    age_matches: list[str] = []
    age_consistency: dict[str, float] = {}
    run_match_threshold = float(config.get("flow_age_run_match_threshold", 12.0))
    jump_match_threshold = float(config.get("flow_age_jump_match_threshold", -12.0))
    jump_strength_min = float(config.get("flow_age_jump_strength_min", 0.18))
    jump_efficiency_max = float(config.get("flow_age_jump_efficiency_max", 0.45))
    mixed_band = abs(float(config.get("flow_age_mixed_band", 28.0)))
    consistency_min = _clamp(float(config.get("flow_age_daily_consistency_min", 0.62)), 0.50, 0.90)

    for label, source, span, points in AGE_STEPS:
        if source == "minute":
            shape = shapes.get(f"{span}m")
            if shape is None:
                shape = _minute_shape(
                    minute_rows,
                    span,
                    typical_daily_range_pct=typical_daily_range,
                    scale=scale,
                )
                shapes[f"{span}m"] = shape
            consistency = 1.0
        else:
            shape = _daily_shape(
                daily_rows,
                span,
                typical_daily_range_pct=typical_daily_range,
                scale=scale,
            )
            shapes[label] = shape
            consistency = _daily_rolling_consistency(
                daily_rows,
                days=span,
                current_regime=regime,
                current_direction=direction,
                typical_daily_range_pct=typical_daily_range,
                scale=scale,
                run_match_threshold=run_match_threshold,
                jump_match_threshold=jump_match_threshold,
                jump_strength_min=jump_strength_min,
                jump_efficiency_max=jump_efficiency_max,
                mixed_band=mixed_band,
            )
        age_consistency[label] = round(consistency, 4)
        if not _age_match(
            current_regime=regime,
            current_direction=direction,
            shape=shape,
            run_match_threshold=run_match_threshold,
            jump_match_threshold=jump_match_threshold,
            jump_strength_min=jump_strength_min,
            jump_efficiency_max=jump_efficiency_max,
            mixed_band=mixed_band,
        ):
            break
        if source == "daily" and span >= 7 and consistency < consistency_min:
            break
        age_score += points
        age_matches.append(label)

    age_score = int(_clamp(float(age_score), 0.0, 99.0))
    return FlowResult(
        available=True,
        score=round(current_score, 4),
        raw_efficiency_ratio=round(current_er, 6),
        age_available=True,
        age_score=float(age_score),
        regime=regime,
        direction=direction,
        horizons={key: value.to_dict() for key, value in shapes.items()},
        age_matches=tuple(age_matches),
        age_consistency=age_consistency,
        reason="weighted 30m/2h/5h path structure plus consecutive and rolling-consistent 2h→30d persistence",
    )
