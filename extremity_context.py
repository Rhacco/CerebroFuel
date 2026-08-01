"""Multi-horizon extension and crowding score for CF v3.9.2.

Positive values mean unusually extended upward; negative values mean unusually
extended downward.  The score combines intraday displacement with 1/3/7-day
swing displacement and current funding crowding.  It describes state, not a
reversal probability, and never creates a trade direction by itself.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _pct(start: float, end: float) -> float:
    return 0.0 if start <= 0 else (end / start - 1.0) * 100.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    value = int(_f(row.get("t")))
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _contiguous(rows: Sequence[Mapping[str, Any]]) -> bool:
    return all(
        _timestamp_ms(current) - _timestamp_ms(previous) == 60_000
        for previous, current in zip(rows, rows[1:])
    )


def _robust_noise(rows: Sequence[Mapping[str, Any]], floor: float) -> float:
    closes = [_f(row.get("c")) for row in rows]
    returns = [
        abs(_pct(left, right))
        for left, right in zip(closes, closes[1:])
        if left > 0 and right > 0
    ]
    return max(floor, statistics.median(returns) * 1.4826 if returns else floor)


def _normalised_move(move: float, expected: float, scale: float = 2.15) -> float:
    return 100.0 * math.tanh(move / max(expected * scale, 1e-9))


def _move_component(rows: Sequence[Mapping[str, Any]], minutes: int, noise: float) -> float:
    if len(rows) < minutes + 1:
        return 0.0
    move = _pct(_f(rows[-minutes - 1].get("c")), _f(rows[-1].get("c")))
    return _normalised_move(move, max(0.025, noise * math.sqrt(minutes)))


def _vwap_component(rows: Sequence[Mapping[str, Any]], noise: float) -> float:
    window = list(rows[-60:])
    volume = sum(max(0.0, _f(row.get("V"))) for row in window)
    if volume <= 0:
        return 0.0
    vwap = sum(
        ((_f(row.get("h")) + _f(row.get("l")) + _f(row.get("c"))) / 3.0)
        * max(0.0, _f(row.get("V")))
        for row in window
    ) / volume
    current = _f(window[-1].get("c"))
    deviation = _pct(vwap, current)
    expected = max(0.04, noise * math.sqrt(60))
    return 100.0 * math.tanh(deviation / max(expected * 1.35, 1e-9))


def _range_component(rows: Sequence[Mapping[str, Any]], count: int) -> float:
    window = list(rows[-count:])
    low = min((_f(row.get("l")) for row in window), default=0.0)
    high = max((_f(row.get("h")) for row in window), default=0.0)
    current = _f(window[-1].get("c")) if window else 0.0
    if low <= 0 or high <= low or current <= 0:
        return 0.0
    position = _clamp((current - low) / (high - low), 0.0, 1.0)
    return (position * 2.0 - 1.0) * 100.0


def _daily_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    clean = [row for row in rows if _f(row.get("c")) > 0 and _timestamp_ms(row) > 0]
    return sorted(clean, key=_timestamp_ms)


def _nearest_close(
    rows: Sequence[Mapping[str, Any]], target_ms: int, tolerance_hours: float = 40.0
) -> float | None:
    tolerance = int(tolerance_hours * 3_600_000)
    candidates = [
        (abs(_timestamp_ms(row) - target_ms), _f(row.get("c")))
        for row in rows
        if _timestamp_ms(row) > 0
        and _f(row.get("c")) > 0
        and abs(_timestamp_ms(row) - target_ms) <= tolerance
    ]
    return min(candidates, default=(0, 0.0), key=lambda item: item[0])[1] or None


def _swing_component(
    rows: Sequence[Mapping[str, Any]],
    current_price: float,
    current_timestamp_ms: int,
) -> tuple[bool, float, float | None, float | None, float | None]:
    daily = _daily_rows(rows)
    if len(daily) < 9 or current_price <= 0:
        return False, 0.0, None, None, None
    baseline = daily[-32:-1] if len(daily) >= 33 else daily[:-1]
    daily_noise = _robust_noise(baseline, 0.35)
    if current_timestamp_ms <= 0:
        current_timestamp_ms = _timestamp_ms(daily[-1])
    returns: dict[int, float] = {}
    scores: dict[int, float] = {}
    for days in (1, 3, 7):
        start = _nearest_close(
            daily,
            current_timestamp_ms - days * 86_400_000,
        )
        if start is None:
            return False, 0.0, None, None, None
        move = _pct(start, current_price)
        returns[days] = move
        scores[days] = _normalised_move(
            move,
            max(0.45, daily_noise * math.sqrt(days)),
            scale=2.05,
        )
    move_score = scores[1] * 0.45 + scores[3] * 0.35 + scores[7] * 0.20
    range_rows = daily[-8:] + [{"h": current_price, "l": current_price, "c": current_price}]
    range_score = _range_component(range_rows, len(range_rows))
    swing = _clamp(move_score * 0.78 + range_score * 0.22, -100.0, 100.0)
    return True, swing, returns[1], returns[3], returns[7]


@dataclass(frozen=True)
class ExtremityResult:
    available: bool = False
    score: float = 0.0
    confidence: float = 0.0
    intraday_score: float = 0.0
    swing_score: float = 0.0
    swing_available: bool = False
    funding_crowding: float = 0.0
    momentum: float = 0.0
    vwap_deviation: float = 0.0
    range_position: float = 0.0
    return_1d: float | None = None
    return_3d: float | None = None
    return_7d: float | None = None
    regime_adjustment: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_extremity(
    *,
    candles: Sequence[Mapping[str, Any]],
    daily_candles: Sequence[Mapping[str, Any]],
    current_price: float,
    current_timestamp_ms: int,
    funding_hourly_pct: float | None,
    tape_quality: float,
    volume_confirmation: float,
    regime_score: float | None = None,
    funding_hard_hourly_pct: float = 0.05,
) -> ExtremityResult:
    """Return a bounded -100..+100 multi-horizon state score.

    Unsigned quote volume changes confidence only.  The 7/14/30-day regime is
    deliberately not subtracted from the score: persistent weakness may be
    normal for a downtrend, but it must not hide a genuine multi-day extension.
    """
    rows = [
        row for row in candles[-300:]
        if _f(row.get("c")) > 0 and _timestamp_ms(row) > 0
    ]
    if len(rows) < 120 or not _contiguous(rows):
        return ExtremityResult(reason="intraday history incomplete")

    baseline_rows = rows[-260:-5] if len(rows) >= 265 else rows[:-5]
    noise = _robust_noise(baseline_rows, 0.015)
    momentum = (
        _move_component(rows, 5, noise) * 0.25
        + _move_component(rows, 20, noise) * 0.35
        + _move_component(rows, 60, noise) * 0.40
    )
    vwap = _vwap_component(rows, noise)
    range_position = _range_component(rows, 180)
    intraday = _clamp(
        momentum * 0.45 + vwap * 0.35 + range_position * 0.20,
        -100.0,
        100.0,
    )

    swing_available, swing, ret1, ret3, ret7 = _swing_component(
        daily_candles, current_price, current_timestamp_ms
    )
    funding = 0.0
    if funding_hourly_pct is not None:
        scale = max(0.005, abs(funding_hard_hourly_pct))
        funding = 100.0 * math.tanh(float(funding_hourly_pct) / scale)

    if swing_available:
        raw = intraday * 0.58 + swing * 0.34 + funding * 0.08
    else:
        raw = intraday * 0.92 + funding * 0.08

    history_confidence = _clamp((len(rows) - 120) / 180.0, 0.0, 1.0) * 25.0 + 45.0
    swing_confidence = 18.0 if swing_available else 0.0
    quality_confidence = _clamp(tape_quality, 0.0, 100.0) * 0.25
    volume_confidence = _clamp(volume_confirmation + 15.0, 0.0, 100.0) * 0.12
    confidence = _clamp(
        history_confidence * 0.55 + swing_confidence + quality_confidence + volume_confidence,
        0.0,
        100.0,
    )
    reliability_scale = 0.76 + confidence / 100.0 * 0.24
    score = _clamp(raw * reliability_scale, -100.0, 100.0)
    return ExtremityResult(
        available=True,
        score=round(score, 4),
        confidence=round(confidence, 4),
        intraday_score=round(intraday, 4),
        swing_score=round(swing, 4),
        swing_available=swing_available,
        funding_crowding=round(funding, 4),
        momentum=round(momentum, 4),
        vwap_deviation=round(vwap, 4),
        range_position=round(range_position, 4),
        return_1d=None if ret1 is None else round(ret1, 4),
        return_3d=None if ret3 is None else round(ret3, 4),
        return_7d=None if ret7 is None else round(ret7, 4),
        regime_adjustment=0.0,
        reason="intraday + 1/3/7D extension/crowding; not reversal probability",
    )


def extremity_color(score: float, available: bool = True) -> str:
    if not available:
        return "⚫"
    if score >= 60.0:
        return "🔴"
    if score >= 20.0:
        return "🟠"
    if score > -20.0:
        return "🟡"
    if score > -60.0:
        return "🔵"
    return "🟢"


def extremity_code(score: float, available: bool = True) -> str:
    if not available:
        return "X?"
    value = min(99, int(round(abs(score))))
    if score >= 20.0:
        return f"OB{value:02d}"
    if score <= -20.0:
        return f"OS{value:02d}"
    rounded = int(round(score))
    if rounded >= 20:
        return f"OB{rounded:02d}"
    if rounded <= -20:
        return f"OS{abs(rounded):02d}"
    return f"X{rounded:+03d}"


# Package revision: v3.9.2-early-build-timing-r1
