"""Persistent category trend cache for v3.5.

The current category score is robustly built from medians and breadth.  This
module adds a short history so declining category demand can confirm sell
warnings without letting one noisy run trigger them.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

STATE_VERSION = "category-v350-trend-r1"


@dataclass(frozen=True)
class CategoryTrend:
    code: str
    fading_score: float
    strengthening_score: float
    confidence: float
    data_quality: str
    score_changes: dict[int, float | None]
    demand_changes: dict[int, float | None]
    positive_breadth_changes: dict[int, float | None]
    negative_breadth_changes: dict[int, float | None]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"version": STATE_VERSION, "categories": {}}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "categories": {}}
    categories = raw.get("categories")
    if not isinstance(categories, dict):
        categories = {}
    return {"version": STATE_VERSION, "categories": categories}


def _save(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _nearest_snapshot(
    snapshots: list[dict[str, Any]],
    target_ms: int,
    tolerance_ms: int,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in snapshots
        if isinstance(item, dict)
        and isinstance(item.get("timestamp_ms"), (int, float))
        and abs(int(item["timestamp_ms"]) - target_ms) <= tolerance_ms
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs(int(item["timestamp_ms"]) - target_ms))


def _weighted_component(values: dict[int, float | None], scales: Mapping[int, float]) -> float:
    weights = {15: 0.25, 30: 0.45, 60: 0.30}
    available: list[tuple[float, float]] = []
    for window, weight in weights.items():
        value = values.get(window)
        if value is None:
            continue
        scale = max(1e-9, float(scales.get(window, 1.0)))
        available.append((_clamp(float(value) / scale), weight))
    if not available:
        return 0.0
    total = sum(weight for _, weight in available)
    return sum(value * weight for value, weight in available) / total


def update_category_state(
    *,
    path: Path,
    assessments: Mapping[str, Any],
    now_ms: int,
    config: Mapping[str, Any],
) -> tuple[dict[str, CategoryTrend], dict[str, Any]]:
    section = config.get("category_trend") if isinstance(config, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    enabled = bool(section.get("enabled", True))
    windows = tuple(int(value) for value in section.get("windows_minutes", [15, 30, 60]))
    windows = tuple(value for value in windows if value in {15, 30, 60}) or (15, 30, 60)
    retention_minutes = max(90, int(section.get("retention_minutes", 240)))
    tolerance_fraction = max(0.15, min(0.60, float(section.get("tolerance_fraction", 0.40))))
    state = _load(path)
    raw_categories = state.setdefault("categories", {})
    cutoff = now_ms - retention_minutes * 60_000
    trends: dict[str, CategoryTrend] = {}

    for code, assessment in assessments.items():
        key = str(code).upper()
        history = raw_categories.get(key)
        if not isinstance(history, list):
            history = []
        snapshots = [
            item for item in history
            if isinstance(item, dict) and int(item.get("timestamp_ms") or 0) >= cutoff
        ]
        snapshots.sort(key=lambda item: int(item.get("timestamp_ms") or 0))

        score_changes: dict[int, float | None] = {}
        demand_changes: dict[int, float | None] = {}
        positive_changes: dict[int, float | None] = {}
        negative_changes: dict[int, float | None] = {}
        found = 0
        current_score = float(getattr(assessment, "score", 50.0))
        current_demand = float(getattr(assessment, "median_demand", 50.0))
        current_positive = float(getattr(assessment, "positive_breadth", 0.0))
        current_negative = float(getattr(assessment, "negative_breadth", 0.0))
        coverage = _clamp(float(getattr(assessment, "coverage", 0.0)))

        for window in windows:
            target = now_ms - window * 60_000
            tolerance = max(4 * 60_000, int(window * tolerance_fraction * 60_000))
            prior = _nearest_snapshot(snapshots, target, tolerance)
            if prior is None:
                score_changes[window] = None
                demand_changes[window] = None
                positive_changes[window] = None
                negative_changes[window] = None
                continue
            found += 1
            score_changes[window] = current_score - float(prior.get("score", current_score))
            demand_changes[window] = current_demand - float(prior.get("demand", current_demand))
            positive_changes[window] = current_positive - float(prior.get("positive_breadth", current_positive))
            negative_changes[window] = current_negative - float(prior.get("negative_breadth", current_negative))

        confidence = _clamp((found / len(windows)) * (0.55 + 0.45 * coverage)) if enabled else 0.0
        score_drop = _weighted_component(
            {window: None if score_changes.get(window) is None else -float(score_changes[window]) for window in windows},
            {15: 5.0, 30: 8.0, 60: 12.0},
        )
        demand_drop = _weighted_component(
            {window: None if demand_changes.get(window) is None else -float(demand_changes[window]) for window in windows},
            {15: 8.0, 30: 12.0, 60: 18.0},
        )
        positive_drop = _weighted_component(
            {window: None if positive_changes.get(window) is None else -float(positive_changes[window]) for window in windows},
            {15: 0.12, 30: 0.18, 60: 0.25},
        )
        negative_rise = _weighted_component(
            {window: negative_changes.get(window) for window in windows},
            {15: 0.10, 30: 0.15, 60: 0.22},
        )
        current_weak = _clamp((48.0 - current_score) / 24.0)
        fading_raw = 100.0 * (
            0.32 * score_drop
            + 0.24 * demand_drop
            + 0.18 * positive_drop
            + 0.18 * negative_rise
            + 0.08 * current_weak
        )
        fading = fading_raw * (0.50 + 0.50 * confidence)
        if found == 0:
            fading = 0.0
        elif found == 1:
            fading = min(fading, 56.0)

        score_rise = _weighted_component(score_changes, {15: 5.0, 30: 8.0, 60: 12.0})
        demand_rise = _weighted_component(demand_changes, {15: 8.0, 30: 12.0, 60: 18.0})
        positive_rise = _weighted_component(positive_changes, {15: 0.12, 30: 0.18, 60: 0.25})
        negative_drop = _weighted_component(
            {window: None if negative_changes.get(window) is None else -float(negative_changes[window]) for window in windows},
            {15: 0.10, 30: 0.15, 60: 0.22},
        )
        strengthening = 100.0 * (
            0.34 * score_rise + 0.26 * demand_rise + 0.22 * positive_rise + 0.18 * negative_drop
        ) * (0.50 + 0.50 * confidence)
        if found == 0:
            strengthening = 0.0

        quality = "good" if found == len(windows) and confidence >= 0.62 else ("partial" if found else "insufficient")
        reasons: list[str] = []
        if fading >= 65.0:
            reasons.append("Kategorie lässt deutlich nach")
        elif fading >= 35.0:
            reasons.append("Kategorie lässt nach")
        if strengthening >= 55.0:
            reasons.append("Kategorie beschleunigt")
        if found < len(windows):
            reasons.append("Kategorieverlauf noch unvollständig")

        trends[key] = CategoryTrend(
            code=key,
            fading_score=round(_clamp(fading / 100.0) * 100.0, 4),
            strengthening_score=round(_clamp(strengthening / 100.0) * 100.0, 4),
            confidence=round(confidence, 4),
            data_quality=quality,
            score_changes={int(k): (None if v is None else round(float(v), 4)) for k, v in score_changes.items()},
            demand_changes={int(k): (None if v is None else round(float(v), 4)) for k, v in demand_changes.items()},
            positive_breadth_changes={int(k): (None if v is None else round(float(v), 5)) for k, v in positive_changes.items()},
            negative_breadth_changes={int(k): (None if v is None else round(float(v), 5)) for k, v in negative_changes.items()},
            reasons=tuple(reasons),
        )

        snapshot = {
            "timestamp_ms": int(now_ms),
            "score": round(current_score, 5),
            "demand": round(current_demand, 5),
            "positive_breadth": round(current_positive, 6),
            "negative_breadth": round(current_negative, 6),
            "coverage": round(coverage, 6),
        }
        if snapshots and abs(int(snapshots[-1].get("timestamp_ms") or 0) - now_ms) < 60_000:
            snapshots[-1] = snapshot
        else:
            snapshots.append(snapshot)
        raw_categories[key] = snapshots[-max(18, retention_minutes // 3):]

    state = {
        "version": STATE_VERSION,
        "updated_at_ms": int(now_ms),
        "categories": raw_categories,
    }
    _save(path, state)
    stats = {
        "version": STATE_VERSION,
        "categories": len(trends),
        "good": sum(value.data_quality == "good" for value in trends.values()),
        "partial": sum(value.data_quality == "partial" for value in trends.values()),
        "insufficient": sum(value.data_quality == "insufficient" for value in trends.values()),
    }
    return trends, stats
# Package revision: v3.6.3-ptw-precision-r3
