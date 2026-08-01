"""Bounded intraday extension/crowding score for CF v3.9.0.

Positive values mean unusually extended upward (overbought/crowded long),
negative values unusually extended downward (oversold/crowded short).  The
score describes the current state; it is not a reversal probability and never
creates a trade direction by itself.
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


def _robust_noise(rows: Sequence[Mapping[str, Any]]) -> float:
    closes = [_f(row.get("c")) for row in rows]
    returns = [
        abs(_pct(left, right))
        for left, right in zip(closes, closes[1:])
        if left > 0 and right > 0
    ]
    return max(0.015, statistics.median(returns) * 1.4826 if returns else 0.015)


def _move_component(rows: Sequence[Mapping[str, Any]], minutes: int, noise: float) -> float:
    if len(rows) < minutes + 1:
        return 0.0
    move = _pct(_f(rows[-minutes - 1].get("c")), _f(rows[-1].get("c")))
    expected = max(0.025, noise * math.sqrt(minutes))
    return 100.0 * math.tanh(move / max(expected * 2.20, 1e-9))


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


def _range_component(rows: Sequence[Mapping[str, Any]]) -> float:
    window = list(rows[-180:])
    low = min((_f(row.get("l")) for row in window), default=0.0)
    high = max((_f(row.get("h")) for row in window), default=0.0)
    current = _f(window[-1].get("c")) if window else 0.0
    if low <= 0 or high <= low or current <= 0:
        return 0.0
    position = _clamp((current - low) / (high - low), 0.0, 1.0)
    return (position * 2.0 - 1.0) * 100.0


@dataclass(frozen=True)
class ExtremityResult:
    available: bool = False
    score: float = 0.0
    confidence: float = 0.0
    momentum: float = 0.0
    vwap_deviation: float = 0.0
    range_position: float = 0.0
    funding_crowding: float = 0.0
    regime_adjustment: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_extremity(
    *,
    candles: Sequence[Mapping[str, Any]],
    funding_hourly_pct: float | None,
    tape_quality: float,
    volume_confirmation: float,
    regime_score: float | None = None,
    funding_hard_hourly_pct: float = 0.05,
) -> ExtremityResult:
    """Return a bounded -100..+100 state score from independent price features.

    Quote volume affects confidence only, because candle volume has no buy/sell
    sign.  A multi-week regime gently detrends the state so that an ordinary
    continuation is not mislabeled as an extreme merely because it persists.
    """
    rows = [
        row for row in candles[-240:]
        if _f(row.get("c")) > 0 and _timestamp_ms(row) > 0
    ]
    if len(rows) < 120 or not _contiguous(rows):
        return ExtremityResult(reason="intraday history incomplete")

    baseline_rows = rows[-220:-5] if len(rows) >= 225 else rows[:-5]
    noise = _robust_noise(baseline_rows)
    momentum = (
        _move_component(rows, 5, noise) * 0.25
        + _move_component(rows, 20, noise) * 0.35
        + _move_component(rows, 60, noise) * 0.40
    )
    vwap = _vwap_component(rows, noise)
    range_position = _range_component(rows)
    funding = 0.0
    if funding_hourly_pct is not None:
        scale = max(0.005, abs(funding_hard_hourly_pct))
        funding = 100.0 * math.tanh(float(funding_hourly_pct) / scale)

    raw = (
        momentum * 0.44
        + vwap * 0.28
        + range_position * 0.20
        + funding * 0.08
    )
    regime_adjustment = 0.0
    if regime_score is not None:
        # A strong established regime makes same-direction displacement less
        # unusual, but can never erase more than 12 points of an intraday extreme.
        regime_adjustment = _clamp(-float(regime_score) * 0.12, -12.0, 12.0)
        raw += regime_adjustment

    history_confidence = _clamp((len(rows) - 120) / 120.0, 0.0, 1.0) * 30.0 + 40.0
    quality_confidence = _clamp(tape_quality, 0.0, 100.0) * 0.35
    volume_confidence = _clamp(volume_confirmation + 15.0, 0.0, 100.0) * 0.25
    confidence = _clamp(history_confidence * 0.40 + quality_confidence + volume_confidence, 0.0, 100.0)
    reliability_scale = 0.72 + confidence / 100.0 * 0.28
    score = _clamp(raw * reliability_scale, -100.0, 100.0)
    return ExtremityResult(
        available=True,
        score=round(score, 4),
        confidence=round(confidence, 4),
        momentum=round(momentum, 4),
        vwap_deviation=round(vwap, 4),
        range_position=round(range_position, 4),
        funding_crowding=round(funding, 4),
        regime_adjustment=round(regime_adjustment, 4),
        reason="intraday extension/crowding; not reversal probability",
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
    return f"X{int(round(score)):+03d}"
