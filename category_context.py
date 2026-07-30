"""Category rotation, laggard context and mixed signal selection for v3.5.

The category layer is deliberately robust: category strength is based on
medians, breadth and stability across all available members, so one isolated
pump cannot turn a weak category green.  Every active coin has exactly one
primary category for quota and ranking purposes.
"""

# v3.5: one-minute category rotation, BTC anchor and adaptive 1–8 signal density.
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
        return YELLOW
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
    defaults = {PURPLE: 4, GREEN: 3, BLUE: 2, YELLOW: 1, ORANGE: 0, RED: 0}
    names = {PURPLE: "purple", GREEN: "green", BLUE: "blue", YELLOW: "yellow", ORANGE: "orange", RED: "red"}
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

        reference_display = str(
            ((config.get("reference_coin") or {}).get("display") if isinstance(config.get("reference_coin"), Mapping) else config.get("reference_coin"))
            or "BTC"
        ).upper()
        exclude_reference = bool(
            (config.get("category_rotation") or {}).get("exclude_reference_from_category_score", True)
        )
        scoring_members = [
            display for display in members
            if not (exclude_reference and display == reference_display)
        ]
        rows = [member_rows[display] for display in scoring_members]
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
        strength = 0 if color in {YELLOW, ORANGE, RED} else min(8, max(1, int(round(score / 12.5))))
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
        # Category-relative value must remain measurable even when the coin has
        # not suffered a large absolute drawdown.  The previous gate required a
        # very high base score before laggard status was calculated at all; in
        # production that collapsed virtually every laggard score to zero while
        # the category header was green.  We now score modest, stable relative
        # underperformance continuously, while still rejecting falling coins.
        eligible_laggard = (
            category.score >= 48.0
            and row["stable"]
            and float(row["demand"]) >= 38.0
            and float(row["base"]) >= 32.0
            and float(row["p30"]) >= -0.55
            and float(row["p60"]) >= -1.10
        )
        if eligible_laggard:
            gap_score = _clamp((combined_lag - 0.03) / 1.25)
            demand_confirm = _clamp((float(row["demand"]) - 38.0) / 42.0)
            base_confirm = _clamp((float(row["base"]) - 32.0) / 48.0)
            activity_confirm = _clamp(float(row["activity"]) / 100.0)
            category_confirm = _clamp((category.score - 45.0) / 35.0)
            laggard = 100.0 * gap_score * (
                0.36 + 0.22 * demand_confirm + 0.18 * base_confirm + 0.12 * activity_confirm + 0.12 * category_confirm
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
    btc_color: str,
    generated_at: datetime,
    timezone: str,
) -> str:
    section = config.get("category_rotation") if isinstance(config, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    order = [str(value).upper() for value in section.get("header_order", ["BTC", "PAY", "SCP", "UTL", "AI", "MEM"])]
    pieces: list[str] = []
    for code in order:
        if code == "BTC":
            pieces.append(f"BTC{btc_color}")
        elif code in assessments:
            pieces.append(f"{code}{assessments[code].color}")
    line = "".join(pieces)
    minute = generated_at.astimezone(ZoneInfo(timezone)).strftime(":%M")
    return line + minute


def select_category_entries(
    analyses: Sequence[Any],
    assessments_by_coin: Mapping[str, Mapping[str, Any]],
    categories: Mapping[str, CategoryAssessment],
    *,
    signal_states: Mapping[str, Any] | None = None,
    state_stats: Mapping[str, Any] | None = None,
    top_count: int,
    config: Mapping[str, Any],
) -> list[Any]:
    """Return an adaptive 1–8 list with independent buy/sell leaders.

    The strongest overall signal is always retained.  Buy and sell sides are
    then evaluated independently, so up to two close leaders from each side can
    appear together when the rest of the market is quiet.  Rows beyond that
    balanced close group still require genuinely strong, confirmed or rapidly
    accelerating evidence.  This preserves a meaningful full list without
    hiding an equally notable signal in the opposite direction.
    """
    thresholds = config.get("opportunity_score") if isinstance(config, Mapping) else {}
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    selection = config.get("selection") if isinstance(config, Mapping) else {}
    selection = selection if isinstance(selection, Mapping) else {}
    max_exits = max(0, int(selection.get("maximum_exit_rows", thresholds.get("maximum_exit_rows", 4))))
    max_exit_per_category = max(1, int(selection.get(
        "maximum_exit_rows_per_category", thresholds.get("maximum_exit_rows_per_category", 2)
    )))
    close_ratio = max(0.0, min(1.0, float(selection.get("close_tie_ratio", 0.90))))
    close_gap = max(0.0, float(selection.get("close_tie_gap", 5.0)))
    close_per_side = max(1, min(2, int(selection.get("close_tie_maximum_per_side", 2))))
    balanced_close_maximum = max(1, min(top_count, int(selection.get("balanced_close_maximum", 4))))
    strong_minimum = max(0.0, float(selection.get("strong_minimum_score", 55.0)))
    leader_gap = max(close_gap, float(selection.get("maximum_leader_gap", 12.0)))
    strong_velocity = max(0.0, float(selection.get("strong_velocity", 4.0)))
    exceptional_demand = max(0.0, float(selection.get("exceptional_demand_score", 65.0)))
    exceptional_execution = max(0.0, float(selection.get("exceptional_execution_quality", 60.0)))
    warmup_runs = max(0, int(selection.get("warmup_runs", 2)))
    warmup_maximum = max(1, min(top_count, int(selection.get("warmup_maximum", 3))))
    warmup_score = max(strong_minimum, float(selection.get("warmup_exception_score", 70.0)))
    run_count = max(0, int((state_stats or {}).get("run_count") or 0))
    signal_states = signal_states or {}

    def state_for(item: Any) -> Any | None:
        return signal_states.get(str(getattr(item, "display_code", "")).upper())

    def score_for(item: Any) -> float:
        return float(getattr(state_for(item), "ranking_score", getattr(item, "opportunity_score", 0.0)))

    def is_close(score: float, reference: float) -> bool:
        return bool(score >= reference * close_ratio or reference - score <= close_gap)

    candidates = sorted(
        analyses,
        key=lambda item: (
            score_for(item),
            float(max(getattr(item, "entry_score", 0.0), getattr(item, "exit_score", 0.0))),
            float(getattr(item, "opportunity_data_confidence", 0.0)),
            str(getattr(item, "display_code", "")),
        ),
        reverse=True,
    )

    entry_counts: dict[str, int] = {}
    exit_counts: dict[str, int] = {}
    exit_total = 0

    def side_for(item: Any) -> tuple[bool, bool, str]:
        display = str(getattr(item, "display_code", "")).upper()
        context = assessments_by_coin.get(display) or {}
        state = state_for(item)
        code = str(context.get("category_code") or "")
        formal_entry = bool(getattr(state, "qualified_entry", context.get("qualified_entry", False)))
        candidate_entry = bool(
            context.get("buy_candidate_ready", getattr(item, "buy_candidate_ready", False))
            and not bool(getattr(item, "falling_knife", False))
            and not bool(getattr(item, "late_entry", False))
            and bool(getattr(item, "exact_interval_volume", False))
            and float(getattr(item, "opportunity_data_confidence", 0.0)) >= 0.55
        )
        formal_exit = bool(getattr(state, "qualified_exit", context.get("qualified_exit", False)))
        if formal_exit and not formal_entry:
            candidate_entry = False
        # Safe blue candidates participate on the buy side even when a formal sell
        # exists elsewhere.  This prevents one orange row from suppressing all
        # healthy buy candidates in a broadly positive category header.
        return (formal_entry or candidate_entry, formal_exit, code)

    def direction_for(item: Any) -> str:
        is_entry, is_exit, _ = side_for(item)
        if is_entry and not is_exit:
            return "▲"
        if is_exit and not is_entry:
            return "▼"
        state = state_for(item)
        direction = str(getattr(state, "direction", getattr(item, "opportunity_direction", "=")))
        if direction in {"▲", "▼"}:
            return direction
        return "▲" if float(getattr(item, "entry_score", 0.0)) >= float(getattr(item, "exit_score", 0.0)) else "▼"

    def context_for(item: Any) -> Mapping[str, Any]:
        return assessments_by_coin.get(str(getattr(item, "display_code", "")).upper()) or {}

    def safe_buy_candidate(item: Any) -> bool:
        context = context_for(item)
        return bool(
            context.get("buy_candidate_ready", getattr(item, "buy_candidate_ready", False))
            and not bool(getattr(item, "falling_knife", False))
            and not bool(getattr(item, "late_entry", False))
            and bool(getattr(item, "exact_interval_volume", False))
            and float(getattr(item, "opportunity_data_confidence", 0.0)) >= 0.55
            and float(getattr(item, "room_to_target_score", 0.0)) >= 18.0
        )

    def can_add(item: Any) -> bool:
        nonlocal exit_total
        is_entry, is_exit, code = side_for(item)
        if is_entry:
            category = categories.get(code)
            return bool(category and category.max_slots > 0 and entry_counts.get(code, 0) < category.max_slots)
        if is_exit:
            return exit_total < max_exits and exit_counts.get(code, 0) < max_exit_per_category
        return False

    def add_item(selected: list[Any], item: Any) -> None:
        nonlocal exit_total
        is_entry, is_exit, code = side_for(item)
        context = context_for(item)
        state = state_for(item)
        formal_entry = bool(getattr(state, "qualified_entry", context.get("qualified_entry", False)))
        candidate_entry = bool(context.get("buy_candidate_ready", getattr(item, "buy_candidate_ready", False)))
        # A selected safe candidate must be rendered as the buy side even when its
        # unqualified raw exit score was numerically a little higher.
        if is_entry and candidate_entry and not formal_entry and not is_exit:
            item.opportunity_direction = "▲"
            item.opportunity_color = BLUE
            item.opportunity_count = max(1, min(8, int(round(float(getattr(item, "entry_score", 0.0)) / 12.5))))
            item.opportunity_score = max(float(getattr(item, "entry_score", 0.0)), float(getattr(item, "buy_candidate_score", 0.0)))
            item.qualified_entry = True
            item.qualified_exit = False
            item.short.signal_color = BLUE
            item.short.direction = "▲"
            item.short.pressure_color = BLUE
            item.short.buy_count = item.opportunity_count
            item.short.sell_count = 0
        selected.append(item)
        if is_entry:
            entry_counts[code] = entry_counts.get(code, 0) + 1
        elif is_exit:
            exit_total += 1
            exit_counts[code] = exit_counts.get(code, 0) + 1

    qualified = [item for item in candidates if any(side_for(item)[:2])]
    selected: list[Any] = []
    if qualified:
        leader = next((item for item in qualified if can_add(item)), None)
        if leader is not None:
            add_item(selected, leader)
            leader_score = score_for(leader)

            # Build the quiet-market close group independently for both sides.
            # This permits, for example, two close buys and two close sells to
            # appear together instead of allowing one direction to crowd out
            # the other before the stronger broad-market rules are evaluated.
            for direction in ("▲", "▼"):
                side_candidates = [
                    item for item in qualified
                    if direction_for(item) == direction
                    and (item in selected or can_add(item))
                ]
                if not side_candidates:
                    continue
                side_reference = next((item for item in side_candidates if item in selected), side_candidates[0])
                side_reference_score = score_for(side_reference)
                side_selected = sum(1 for item in selected if direction_for(item) == direction)
                for item in side_candidates:
                    if item in selected or len(selected) >= balanced_close_maximum or side_selected >= close_per_side:
                        continue
                    if not can_add(item):
                        continue
                    score = score_for(item)
                    if not is_close(score, leader_score) or not is_close(score, side_reference_score):
                        continue
                    add_item(selected, item)
                    side_selected += 1

            # If the header is broadly positive but the only formal signal is a
            # sell, retain the strongest genuinely safe blue buy candidate too.
            # The candidate still passed discount, stability, demand, execution
            # and anti-falling-knife checks; it is not a neutral filler.
            if not any(direction_for(item) == "▲" for item in selected):
                safe_buys = [
                    item for item in candidates
                    if item not in selected and direction_for(item) == "▲" and safe_buy_candidate(item) and can_add(item)
                ]
                if safe_buys:
                    add_item(selected, safe_buys[0])

            # Rows beyond the balanced close group must be materially strong.
            # A broad market move can still fill all eight rows, but ordinary
            # threshold crossings no longer do so automatically.
            for item in qualified:
                if item in selected or len(selected) >= top_count or not can_add(item):
                    continue
                score = score_for(item)
                if leader_score - score > leader_gap:
                    continue
                state = state_for(item)
                confirmation = int(getattr(state, "confirmation_count", 0))
                velocity = float(getattr(state, "score_velocity", 0.0))
                color = str(getattr(state, "color", ""))
                direction = direction_for(item)
                exact = bool(getattr(item, "exact_interval_volume", False))
                execution = float(getattr(item, "execution_quality_score", 0.0))
                demand = float(getattr(item, "demand_score", 0.0))
                exit_score = float(getattr(item, "exit_score", 0.0))
                exceptional_exact = bool(
                    exact
                    and execution >= exceptional_execution
                    and ((direction == "▲" and demand >= exceptional_demand) or (direction == "▼" and exit_score >= exceptional_demand))
                )
                strong = bool(
                    score >= strong_minimum
                    or confirmation >= 2
                    or velocity >= strong_velocity
                    or color in {GREEN, PURPLE, RED}
                    or exceptional_exact
                )
                if not strong:
                    continue
                if run_count <= warmup_runs and len(selected) >= warmup_maximum:
                    warmup_exception = bool(
                        score >= warmup_score
                        and exceptional_exact
                        and not bool(getattr(item, "falling_knife", False))
                        and not bool(getattr(item, "late_entry", False))
                    )
                    if not warmup_exception:
                        continue
                add_item(selected, item)

            if selected:
                return selected[:top_count]

    # No qualified signal survived: show the clearest real buy/sell side, never
    # a neutral or brown placeholder.  The fallback is balanced independently,
    # too, so close buy and sell leaders can coexist while the market is quiet.
    def safe_buy_fallback(item: Any) -> bool:
        context = context_for(item)
        return bool(
            safe_buy_candidate(item)
            or (
                not bool(getattr(item, "falling_knife", False))
                and not bool(getattr(item, "late_entry", False))
                and bool(getattr(item, "exact_interval_volume", False))
                and float(getattr(item, "category_score", 0.0)) >= 48.0
                and (
                    float(getattr(item, "cheap_price_score", 0.0)) >= 36.0
                    or bool(getattr(item, "relative_discount_qualified", False))
                    or float(getattr(item, "balanced_value_score", 0.0)) >= 15.0
                    or (
                        float(getattr(item, "relative_bargain_score", 0.0)) >= 34.0
                        and float(getattr(item, "laggard_score", 0.0)) >= 8.0
                    )
                )
                and float(getattr(item, "stabilization_score", 0.0)) >= 32.0
                and float(getattr(item, "demand_score", 0.0)) >= 42.0
                and float(getattr(item, "room_to_target_score", 0.0)) >= 18.0
                and bool(context.get("buy_candidate_ready", False))
            )
        )

    def safe_sell_fallback(item: Any) -> bool:
        return bool(
            float(getattr(item, "exit_score", 0.0)) >= 36.0
            and float(getattr(item, "exit_score", 0.0)) >= float(getattr(item, "entry_score", 0.0)) + 3.0
            and (
                bool(getattr(item, "falling_knife", False))
                or float(getattr(item, "category_fading_score", 0.0)) >= 12.0
                or bool(getattr(item, "qualified_exit", False))
            )
        )

    fallback = [
        item for item in candidates
        if bool(getattr(state_for(item), "fallback_eligible", False))
        and ((direction_for(item) == "▲" and safe_buy_fallback(item)) or (direction_for(item) == "▼" and safe_sell_fallback(item)))
    ]
    if not fallback:
        # When no formal signal qualifies, prefer a real sell/avoid warning.  A
        # buy-side fallback remains allowed only after the same discounted and
        # stabilized recovery gate used by normal buy recommendations.
        fallback = [
            item for item in candidates
            if float(getattr(item, "opportunity_data_confidence", 0.0)) >= 0.45
            and (
                (direction_for(item) == "▲" and safe_buy_fallback(item))
                or (direction_for(item) == "▼" and safe_sell_fallback(item))
            )
        ]
    if not fallback and candidates:
        # Last resort still has to be a genuine side.  Never relabel a failed
        # buy gate as a sell merely to force a row into Discord.
        safe_candidates = [
            item for item in candidates
            if safe_buy_fallback(item) or safe_sell_fallback(item)
        ]
        fallback = safe_candidates[:1]
    if not fallback:
        return []

    ratio = float(selection.get("fallback_tie_ratio", (config.get("signal_state") or {}).get("fallback_tie_ratio", 0.90)))
    gap = float(selection.get("fallback_tie_gap", (config.get("signal_state") or {}).get("fallback_tie_gap", 5.0)))

    def fallback_close(score: float, reference: float) -> bool:
        return bool(score >= reference * ratio or reference - score <= gap)

    leader = fallback[0]
    leader_score = score_for(leader)
    chosen = [leader]
    for direction in ("▲", "▼"):
        side_candidates = [item for item in fallback if direction_for(item) == direction]
        if not side_candidates:
            continue
        side_reference = next((item for item in side_candidates if item in chosen), side_candidates[0])
        side_reference_score = score_for(side_reference)
        side_selected = sum(1 for item in chosen if direction_for(item) == direction)
        for item in side_candidates:
            if item in chosen or len(chosen) >= balanced_close_maximum or side_selected >= close_per_side:
                continue
            score = score_for(item)
            if fallback_close(score, leader_score) and fallback_close(score, side_reference_score):
                chosen.append(item)
                side_selected += 1

    valid_chosen: list[Any] = []
    for item in chosen:
        state = state_for(item)
        direction = str(getattr(state, "direction", "▲" if item.entry_score >= item.exit_score else "▼"))
        if direction == "▲" and not safe_buy_fallback(item):
            continue
        if direction == "▼" and not safe_sell_fallback(item):
            continue
        item.opportunity_direction = direction
        item.opportunity_color = "🔵" if direction == "▲" else "🟠"
        item.opportunity_count = 1
        item.opportunity_score = score_for(item)
        item.qualified_entry = direction == "▲"
        item.qualified_exit = direction == "▼"
        item.short.signal_color = item.opportunity_color
        item.short.direction = direction
        item.short.pressure_color = item.opportunity_color
        item.short.buy_count = 1 if direction == "▲" else 0
        item.short.sell_count = 1 if direction == "▼" else 0
        valid_chosen.append(item)
    return valid_chosen[:top_count]
# Package revision: v3.6.1-simple-signals-r1
