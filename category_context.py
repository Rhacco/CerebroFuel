"""Category rotation and laggard context for v3.4.

The category layer is deliberately robust: category strength is based on
medians, breadth and stability across all available members, so one isolated
pump cannot turn a weak category green.  Every active coin has exactly one
primary category for quota and ranking purposes.
"""

# v3.4 r4 expanded-69 rebuild; source revalidated 2026-07-26.
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from market_data import IntradayMetrics

PURPLE = "🟣"
GREEN = "🟢"
BLUE = "🔵"
YELLOW = "🟡"
ORANGE = "🟠"
RED = "🔴"
BROWN = "🟤"


@dataclass(frozen=True)
class CategoryAssessment:
    code: str
    name: str
    score: float
    color: str
    strength_count: int
    max_slots: int
    coverage: float
    positive_breadth: float
    negative_breadth: float
    median_price_30: float
    median_price_60: float
    median_demand: float
    median_base: float
    member_count: int
    valid_count: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoinCategoryContext:
    display: str
    category_code: str
    category_name: str
    category_score: float
    category_color: str
    category_boost: float
    laggard_score: float
    activity_score: float
    event_penalty: float
    max_slots: int
    member_price_30: float
    member_price_60: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _median(values: Sequence[float], default: float = 0.0) -> float:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.median(cleaned) if cleaned else default


def _percentile(values: Sequence[float], fraction: float, default: float = 0.0) -> float:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return default
    position = _clamp(fraction) * (len(cleaned) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return cleaned[low]
    weight = position - low
    return cleaned[low] * (1.0 - weight) + cleaned[high] * weight


def _quality_value(value: str) -> float:
    return {"good": 1.0, "partial": 0.76, "insufficient": 0.0}.get(str(value), 0.0)


def _event_penalty(display: str, config: Mapping[str, Any]) -> tuple[float, str | None]:
    section = config.get("event_risk") if isinstance(config, Mapping) else None
    if not isinstance(section, Mapping) or not bool(section.get("enabled", True)):
        return 0.0, None
    raw = section.get("coin_penalties")
    item = raw.get(display) if isinstance(raw, Mapping) else None
    if isinstance(item, Mapping):
        value = float(item.get("penalty", 0.0))
        reason = str(item.get("reason") or "") or None
    elif item is not None:
        value = float(item)
        reason = None
    else:
        value = 0.0
        reason = None
    maximum = max(0.0, float(section.get("maximum_penalty", 12.0)))
    return min(maximum, max(0.0, value)), reason


def _member_metrics(
    display: str,
    *,
    flash_signals: Mapping[str, Any],
    intraday_by_display: Mapping[str, IntradayMetrics],
) -> dict[str, Any]:
    metrics = intraday_by_display.get(display)
    signal = flash_signals.get(display)
    exact = bool(metrics and metrics.data_quality != "insufficient")
    if exact and metrics is not None:
        p30 = float(metrics.price_changes.get(30) or 0.0)
        p60 = float(metrics.price_changes.get(60) or 0.0)
        p180 = float(metrics.price_changes.get(180) or 0.0)
        demand = float(metrics.demand_score)
        sell = float(metrics.sell_pressure_score)
        base = float(metrics.base_quality_score)
        quality = _quality_value(metrics.data_quality)
        falling = bool(metrics.falling_knife)
        z_values = [
            max(0.0, float(value))
            for value in metrics.volume_z.values()
            if value is not None and math.isfinite(float(value))
        ]
        ratio_values = [
            max(0.0, float(value) - 1.0)
            for value in metrics.volume_ratios.values()
            if value is not None and math.isfinite(float(value))
        ]
        volume_activity = min(
            100.0,
            24.0 * (max(z_values) if z_values else 0.0)
            + 80.0 * (max(ratio_values) if ratio_values else 0.0),
        )
        activity = max(
            0.45 * demand + 0.25 * volume_activity + 0.30 * min(100.0, abs(p30) * 24.0 + abs(p60) * 12.0),
            float(getattr(signal, "volatility_score", 0.0)) if signal else 0.0,
        )
    elif signal is not None:
        p30 = float(signal.price_changes.get(30) or 0.0)
        p60 = float(signal.price_changes.get(60) or 0.0)
        p180 = p60
        demand = float(signal.entry_score)
        sell = float(signal.exit_score)
        base = max(0.0, min(100.0, 72.0 + p30 * 24.0 + p60 * 12.0 - sell * 0.28))
        quality = max(0.0, min(0.72, float(signal.quality)))
        falling = p30 < -0.30 or p60 < -0.55
        activity = max(float(signal.volatility_score), demand, sell * 0.55)
    else:
        p30 = p60 = p180 = demand = sell = activity = 0.0
        base = 50.0
        quality = 0.0
        falling = False
    stable = not falling and p30 >= -0.35 and p60 >= -0.75 and p180 >= -1.60 and base >= 38.0
    positive = stable and ((demand >= 54.0 and p30 >= -0.12) or (p30 >= 0.10 and demand >= 42.0))
    negative = falling or sell >= 62.0 or p60 < -0.95
    return {
        "display": display,
        "p30": p30,
        "p60": p60,
        "p180": p180,
        "demand": max(0.0, min(100.0, demand)),
        "sell": max(0.0, min(100.0, sell)),
        "base": max(0.0, min(100.0, base)),
        "activity": max(0.0, min(100.0, activity)),
        "quality": quality,
        "falling": falling,
        "stable": stable,
        "positive": positive,
        "negative": negative,
    }


def _category_color(score: float, coverage: float, config: Mapping[str, Any]) -> str:
    section = config.get("category_rotation") if isinstance(config, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    if coverage < float(section.get("minimum_color_coverage", 0.40)):
        return BROWN
    thresholds = section.get("thresholds") if isinstance(section.get("thresholds"), Mapping) else {}
    if score >= float(thresholds.get("purple", 78.0)):
        return PURPLE
    if score >= float(thresholds.get("green", 62.0)):
        return GREEN
    if score >= float(thresholds.get("blue", 48.0)):
        return BLUE
    if score >= float(thresholds.get("yellow", 36.0)):
        return YELLOW
    if score >= float(thresholds.get("orange", 24.0)):
        return ORANGE
    return RED


def _max_slots(color: str, config: Mapping[str, Any]) -> int:
    section = config.get("category_rotation") if isinstance(config, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    raw = section.get("max_slots") if isinstance(section.get("max_slots"), Mapping) else {}
    defaults = {PURPLE: 4, GREEN: 3, BLUE: 2, YELLOW: 1, ORANGE: 0, RED: 0, BROWN: 0}
    names = {PURPLE: "purple", GREEN: "green", BLUE: "blue", YELLOW: "yellow", ORANGE: "orange", RED: "red", BROWN: "brown"}
    return max(0, int(raw.get(names[color], defaults[color])))


def build_category_context(
    *,
    config: Mapping[str, Any],
    flash_signals: Mapping[str, Any],
    intraday_by_display: Mapping[str, IntradayMetrics],
) -> tuple[dict[str, CategoryAssessment], dict[str, CoinCategoryContext]]:
    raw_categories = config.get("categories") if isinstance(config, Mapping) else None
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ValueError("config.json benötigt eine nichtleere Kategorienliste.")

    assessments: dict[str, CategoryAssessment] = {}
    member_rows: dict[str, dict[str, Any]] = {}
    owner: dict[str, str] = {}

    for raw_category in raw_categories:
        if not isinstance(raw_category, Mapping):
            raise ValueError("Ungültiger Kategorieeintrag.")
        code = str(raw_category.get("code") or "").upper()
        name = str(raw_category.get("name") or code)
        members = [str(value).upper() for value in raw_category.get("coins", []) if str(value).strip()]
        if not code or len(code) > 3 or not members:
            raise ValueError(f"Ungültige Kategorie: {raw_category!r}")
        for display in members:
            if display in owner:
                raise ValueError(f"Coin {display} ist mehreren Primärkategorien zugeordnet: {owner[display]}, {code}")
            owner[display] = code
            member_rows[display] = _member_metrics(
                display,
                flash_signals=flash_signals,
                intraday_by_display=intraday_by_display,
            )

        rows = [member_rows[display] for display in members]
        valid = [row for row in rows if row["quality"] > 0.0]
        coverage = sum(row["quality"] for row in rows) / max(1, len(rows))
        positive_breadth = sum(bool(row["positive"]) for row in valid) / max(1, len(valid))
        negative_breadth = sum(bool(row["negative"]) for row in valid) / max(1, len(valid))
        p30 = _median([row["p30"] for row in valid])
        p60 = _median([row["p60"] for row in valid])
        demand = _median([row["demand"] for row in valid], 50.0)
        demand_upper = _percentile([row["demand"] for row in valid], 0.75, demand)
        base = _median([row["base"] for row in valid], 50.0)
        activity = _median([row["activity"] for row in valid], 40.0)

        demand_score = 0.64 * demand + 0.36 * demand_upper
        breadth_score = max(0.0, min(100.0, 50.0 + 72.0 * (positive_breadth - negative_breadth)))
        price_score = max(0.0, min(100.0, 50.0 + p30 * 24.0 + p60 * 12.0))
        stability_score = max(0.0, min(100.0, 0.55 * base + 45.0 * (1.0 - negative_breadth)))
        raw_score = (
            0.30 * demand_score
            + 0.25 * breadth_score
            + 0.20 * price_score
            + 0.15 * stability_score
            + 0.10 * activity
        )
        sample_confidence = _clamp(len(valid) / max(3.0, min(6.0, float(len(rows)))))
        confidence = sample_confidence * (0.55 + 0.45 * coverage)
        score = 50.0 + (raw_score - 50.0) * confidence
        color = _category_color(score, coverage, config)
        slots = _max_slots(color, config)
        strength = 0 if color in {YELLOW, ORANGE, RED, BROWN} else min(8, max(2, int(round(score / 12.5))))
        reasons: list[str] = []
        if positive_breadth >= 0.55:
            reasons.append("breite Stärke")
        if negative_breadth >= 0.40:
            reasons.append("breiter Verkaufsdruck")
        if demand_score >= 62.0:
            reasons.append("Nachfrage zieht an")
        if p30 > 0.15 and p60 > 0.20:
            reasons.append("Kursmomentum bestätigt")
        if coverage < 0.55:
            reasons.append("begrenzte Datenabdeckung")
        assessments[code] = CategoryAssessment(
            code=code,
            name=name,
            score=round(score, 4),
            color=color,
            strength_count=strength,
            max_slots=slots,
            coverage=round(coverage, 5),
            positive_breadth=round(positive_breadth, 5),
            negative_breadth=round(negative_breadth, 5),
            median_price_30=round(p30, 5),
            median_price_60=round(p60, 5),
            median_demand=round(demand, 4),
            median_base=round(base, 4),
            member_count=len(rows),
            valid_count=len(valid),
            reasons=tuple(reasons),
        )

    coin_context: dict[str, CoinCategoryContext] = {}
    for display, code in owner.items():
        category = assessments[code]
        row = member_rows[display]
        lag30 = category.median_price_30 - float(row["p30"])
        lag60 = category.median_price_60 - float(row["p60"])
        combined_lag = 0.68 * lag30 + 0.32 * lag60
        eligible_laggard = (
            category.score >= 48.0
            and row["stable"]
            and float(row["demand"]) >= 42.0
            and float(row["base"]) >= 45.0
            and float(row["p30"]) >= -0.35
            and float(row["p60"]) >= -0.75
        )
        if eligible_laggard:
            gap_score = _clamp((combined_lag - 0.10) / 1.70)
            demand_confirm = _clamp((float(row["demand"]) - 42.0) / 38.0)
            base_confirm = _clamp((float(row["base"]) - 45.0) / 35.0)
            activity_confirm = _clamp(float(row["activity"]) / 100.0)
            category_confirm = _clamp((category.score - 45.0) / 35.0)
            laggard = 100.0 * gap_score * (
                0.30 + 0.24 * demand_confirm + 0.20 * base_confirm + 0.14 * activity_confirm + 0.12 * category_confirm
            )
        else:
            laggard = 0.0
        category_boost = max(-14.0, min(11.0, (category.score - 50.0) * 0.30))
        event_penalty, event_reason = _event_penalty(display, config)
        reasons: list[str] = []
        if laggard >= 35.0:
            reasons.append("starker Kategorie-Nachzügler")
        elif laggard >= 15.0:
            reasons.append("Kategorie-Nachzügler")
        if category.score < 36.0:
            reasons.append("Kategorie zu schwach")
        if event_penalty >= 7.0:
            reasons.append(event_reason or "hohes Ereignisrisiko")
        coin_context[display] = CoinCategoryContext(
            display=display,
            category_code=code,
            category_name=category.name,
            category_score=category.score,
            category_color=category.color,
            category_boost=round(category_boost, 4),
            laggard_score=round(max(0.0, min(100.0, laggard)), 4),
            activity_score=round(float(row["activity"]), 4),
            event_penalty=round(event_penalty, 4),
            max_slots=category.max_slots,
            member_price_30=round(float(row["p30"]), 5),
            member_price_60=round(float(row["p60"]), 5),
            reasons=tuple(reasons),
        )
    return assessments, coin_context


def format_category_line(
    assessments: Mapping[str, CategoryAssessment],
    *,
    config: Mapping[str, Any],
    generated_at: datetime,
    timezone: str,
) -> str:
    order = [str(item.get("code") or "").upper() for item in config.get("categories", []) if isinstance(item, Mapping)]
    line = "".join(f"{code}{assessments[code].color}" for code in order if code in assessments)
    minute = generated_at.astimezone(ZoneInfo(timezone)).strftime(":%M")
    return line + minute


def select_category_entries(
    analyses: Sequence[Any],
    assessments_by_coin: Mapping[str, Mapping[str, Any]],
    categories: Mapping[str, CategoryAssessment],
    *,
    top_count: int,
) -> list[Any]:
    """Select only qualified entry setups, respecting live category quotas."""
    ordered = sorted(
        analyses,
        key=lambda item: (
            float(getattr(item, "entry_score", 0.0)),
            float(getattr(item, "opportunity_score", 0.0)),
            float(getattr(item, "opportunity_data_confidence", 0.0)),
            float(getattr(item, "target_prior_score", 50.0)),
            str(getattr(item, "display_code", "")),
        ),
        reverse=True,
    )
    selected: list[Any] = []
    counts: dict[str, int] = {}
    for item in ordered:
        display = str(getattr(item, "display_code", "")).upper()
        context = assessments_by_coin.get(display) or {}
        if not bool(context.get("qualified_entry")):
            continue
        code = str(context.get("category_code") or "")
        category = categories.get(code)
        if category is None or category.max_slots <= 0:
            continue
        if counts.get(code, 0) >= category.max_slots:
            continue
        selected.append(item)
        counts[code] = counts.get(code, 0) + 1
        if len(selected) >= top_count:
            break
    return selected
