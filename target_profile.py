"""Historical +3%/+5% target priors for v3.3.3.

The daily cache already stores LCW price observations.  This module uses only
past points and never looks beyond an anchor's 24-hour horizon.  Sparse history
is explicitly confidence-capped rather than presented as precise intraday data.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

from analysis import PricePoint


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _wilson_lower(successes: int, total: int, z: float = 1.2816) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return _clamp((centre - margin) / denominator)


def _target_result(
    entry: float,
    future: Sequence[PricePoint],
    *,
    target_pct: float,
    stop_pct: float,
) -> tuple[bool, bool, float | None, float, float]:
    """Return hit, stop_first, hours_to_hit, max_favourable, max_adverse."""
    max_favourable = -math.inf
    max_adverse = math.inf
    for point in future:
        change = (point.rate / entry - 1.0) * 100.0
        max_favourable = max(max_favourable, change)
        max_adverse = min(max_adverse, change)
        if change <= -abs(stop_pct):
            return False, True, None, max_favourable, max_adverse
        if change >= target_pct:
            return True, False, None, max_favourable, max_adverse
    return False, False, None, max_favourable, max_adverse


def compute_target_profile(
    points: Sequence[PricePoint],
    *,
    now_ms: int,
    horizon_hours: int = 24,
    minimum_anchor_spacing_hours: int = 12,
) -> dict[str, Any]:
    ordered = sorted(
        {point.timestamp_ms: point for point in points if point.rate > 0}.values(),
        key=lambda point: point.timestamp_ms,
    )
    if len(ordered) < 12:
        return {
            "score": 50.0,
            "confidence": 0.0,
            "samples": 0,
            "hit3_before_stop": None,
            "hit5_before_stop": None,
            "median_hours_to_3": None,
            "median_hours_to_5": None,
            "median_max_adverse_pct": None,
            "resolution_hours": None,
            "method": "insufficient-history",
        }

    horizon_ms = max(1, horizon_hours) * 3_600_000
    spacing_ms = max(1, minimum_anchor_spacing_hours) * 3_600_000
    final_anchor_ms = min(now_ms, ordered[-1].timestamp_ms) - horizon_ms
    last_anchor = -10**30
    anchors: list[tuple[PricePoint, list[PricePoint]]] = []
    gaps: list[float] = []
    for index, anchor in enumerate(ordered[:-1]):
        if anchor.timestamp_ms > final_anchor_ms or anchor.timestamp_ms - last_anchor < spacing_ms:
            continue
        future = [
            point
            for point in ordered[index + 1 :]
            if anchor.timestamp_ms < point.timestamp_ms <= anchor.timestamp_ms + horizon_ms
        ]
        if not future:
            continue
        coverage_hours = (future[-1].timestamp_ms - anchor.timestamp_ms) / 3_600_000.0
        if coverage_hours < min(8.0, horizon_hours * 0.45):
            continue
        local = [anchor, *future]
        gaps.extend(
            (right.timestamp_ms - left.timestamp_ms) / 3_600_000.0
            for left, right in zip(local, local[1:])
            if right.timestamp_ms > left.timestamp_ms
        )
        anchors.append((anchor, future))
        last_anchor = anchor.timestamp_ms

    if not anchors:
        return {
            "score": 50.0,
            "confidence": 0.0,
            "samples": 0,
            "hit3_before_stop": None,
            "hit5_before_stop": None,
            "median_hours_to_3": None,
            "median_hours_to_5": None,
            "median_max_adverse_pct": None,
            "resolution_hours": None,
            "method": "insufficient-covered-anchors",
        }

    hit3 = hit5 = 0
    resolved3 = resolved5 = 0
    times3: list[float] = []
    times5: list[float] = []
    adverse: list[float] = []

    for anchor, future in anchors:
        entry = anchor.rate
        first3: str | None = None
        first5: str | None = None
        max_adverse = 0.0
        for point in future:
            change = (point.rate / entry - 1.0) * 100.0
            max_adverse = min(max_adverse, change)
            elapsed = (point.timestamp_ms - anchor.timestamp_ms) / 3_600_000.0
            if first3 is None:
                if change <= -1.5:
                    first3 = "stop"
                    resolved3 += 1
                elif change >= 3.0:
                    first3 = "hit"
                    resolved3 += 1
                    hit3 += 1
                    times3.append(elapsed)
            if first5 is None:
                if change <= -2.0:
                    first5 = "stop"
                    resolved5 += 1
                elif change >= 5.0:
                    first5 = "hit"
                    resolved5 += 1
                    hit5 += 1
                    times5.append(elapsed)
        # Timeout without either target is a miss, but not a false stop.
        if first3 is None:
            resolved3 += 1
        if first5 is None:
            resolved5 += 1
        adverse.append(max_adverse)

    rate3 = hit3 / resolved3 if resolved3 else 0.0
    rate5 = hit5 / resolved5 if resolved5 else 0.0
    lower3 = _wilson_lower(hit3, resolved3)
    lower5 = _wilson_lower(hit5, resolved5)
    resolution = statistics.median(gaps) if gaps else 24.0
    resolution_confidence = _clamp((12.0 - resolution) / 10.0)
    sample_confidence = _clamp(len(anchors) / 36.0)
    confidence = sample_confidence * (0.30 + 0.70 * resolution_confidence)
    speed3 = 0.0 if not times3 else _clamp((24.0 - statistics.median(times3)) / 20.0)
    speed5 = 0.0 if not times5 else _clamp((24.0 - statistics.median(times5)) / 20.0)
    evidence_score = 100.0 * (0.56 * lower3 + 0.30 * lower5 + 0.09 * speed3 + 0.05 * speed5)
    # Low-resolution LCW histories are a prior only: blend towards neutral.
    score = 50.0 + (evidence_score - 50.0) * confidence

    return {
        "score": round(_clamp(score / 100.0) * 100.0, 4),
        "confidence": round(confidence, 4),
        "samples": len(anchors),
        "resolved3": resolved3,
        "resolved5": resolved5,
        "hit3": hit3,
        "hit5": hit5,
        "hit3_before_stop": round(rate3, 5),
        "hit5_before_stop": round(rate5, 5),
        "wilson3": round(lower3, 5),
        "wilson5": round(lower5, 5),
        "median_hours_to_3": None if not times3 else round(statistics.median(times3), 3),
        "median_hours_to_5": None if not times5 else round(statistics.median(times5), 3),
        "median_max_adverse_pct": None if not adverse else round(statistics.median(adverse), 4),
        "resolution_hours": round(resolution, 3),
        "method": "past-only-24h-target-before-stop-r1",
    }
