"""Three-minute entry/exit opportunity scoring for v3.5."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from analysis import BLUE, GREEN, ORANGE, PURPLE, RED, WHITE, YELLOW, ShortMetrics, color_level
from market_data import IntradayMetrics


@dataclass
class MarketQuality:
    score: float
    color: str
    direction: str
    strength_count: int
    btc_structure_score: float
    btc_demand_score: float
    breadth_score: float
    positive_breadth: float
    negative_breadth: float
    exact_volume: bool
    btc_reference_score: float
    btc_reference_color: str
    reasons: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OpportunityAssessment:
    display: str
    entry_score: float
    exit_score: float
    ranking_score: float
    direction: str
    color: str
    strength_count: int
    demand_score: float
    base_quality_score: float
    cheap_price_score: float
    stabilization_score: float
    recent_drawdown_pct: float
    rebound_from_low_pct: float
    discount_qualified: bool
    stabilized_after_drop: bool
    demand_confirmed: bool
    confirmed_recovery: bool
    positive_gap_score: float
    negative_gap_score: float
    relative_strength_score: float
    room_to_target_score: float
    target_prior_score: float
    target_prior_confidence: float
    liquidity_score: float
    execution_quality_score: float
    spread_pct: float | None
    estimated_round_trip_cost_pct: float
    net_target_quality_score: float
    category_lead_bonus: float
    market_adjustment: float
    unlock_penalty: float
    late_entry_penalty: float
    falling_knife_penalty: float
    exact_volume: bool
    provider: str
    provider_symbol: str | None
    data_confidence: float
    falling_knife: bool
    late_entry: bool
    qualified_entry: bool
    qualified_exit: bool
    category_code: str
    category_score: float
    category_color: str
    category_boost: float
    laggard_score: float
    activity_score: float
    event_penalty: float
    category_fading_score: float
    category_strengthening_score: float
    category_trend_confidence: float
    volume_colors: dict[int, str] = field(default_factory=dict)
    reasons: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _delta_pct(value: Any) -> float:
    try:
        return (float(value) - 1.0) * 100.0
    except (TypeError, ValueError):
        return 0.0


def _profile_score(profile: Mapping[str, Any] | None) -> tuple[float, float]:
    if not isinstance(profile, Mapping):
        return 50.0, 0.0
    try:
        score = _clamp(float(profile.get("score", 50.0)) / 100.0) * 100.0
        confidence = _clamp(float(profile.get("confidence", 0.0)))
    except (TypeError, ValueError):
        return 50.0, 0.0
    return score, confidence


def combine_target_profiles(
    historical: Mapping[str, Any] | None,
    live: Mapping[str, Any] | None,
) -> tuple[float, float]:
    historical_score, historical_conf = _profile_score(historical)
    live_score, live_conf = _profile_score(live)
    total = historical_conf + 1.35 * live_conf
    if total <= 1e-9:
        return 50.0, 0.0
    evidence = (historical_score * historical_conf + live_score * 1.35 * live_conf) / total
    confidence = _clamp(total / 1.8)
    return 50.0 + (evidence - 50.0) * confidence, confidence


def _market_color(score: float) -> str:
    if score >= 68:
        return PURPLE
    if score >= 30:
        return GREEN
    if score >= 8:
        return BLUE
    if score <= -60:
        return RED
    if score <= -15:
        return ORANGE
    return YELLOW


def build_market_quality(
    *,
    btc_intraday: IntradayMetrics | None,
    intraday_by_display: Mapping[str, IntradayMetrics],
    rows_by_display: Mapping[str, Mapping[str, Any]],
    reference_display: str,
) -> MarketQuality:
    btc = btc_intraday or IntradayMetrics(display=reference_display)
    p5 = float(btc.price_changes.get(5) or 0.0)
    p15 = float(btc.price_changes.get(15) or 0.0)
    p60 = float(btc.price_changes.get(60) or 0.0)
    p180 = float(btc.price_changes.get(180) or 0.0)
    structure = 50.0
    structure += max(-16.0, min(16.0, p5 * 24.0))
    structure += max(-25.0, min(25.0, p15 * 16.0))
    structure += max(-25.0, min(25.0, p60 * 8.0))
    structure += max(-18.0, min(18.0, p180 * 3.2))
    if btc.falling_knife:
        structure -= 38.0
    if btc.late_entry:
        structure -= 8.0
    structure = max(0.0, min(100.0, structure))

    demand = float(btc.demand_score if btc.exact_interval_volume else 50.0)
    demand -= 0.55 * float(btc.sell_pressure_score)
    demand = max(0.0, min(100.0, demand))

    usable = [
        item for display, item in intraday_by_display.items()
        if display != reference_display and item.data_quality in {"good", "partial"}
    ]
    positives = sum(
        float(item.demand_score) >= 58.0
        and not item.falling_knife
        and float(item.price_changes.get(30) or 0.0) >= -0.35
        for item in usable
    )
    negatives = sum(
        float(item.sell_pressure_score) >= 48.0 or item.falling_knife
        for item in usable
    )
    positive_breadth = positives / len(usable) if usable else 0.0
    negative_breadth = negatives / len(usable) if usable else 0.0
    fast_breadth = 50.0 + 80.0 * (positive_breadth - negative_breadth)

    day_values = [
        _delta_pct((row.get("delta") or {}).get("day"))
        for display, row in rows_by_display.items()
        if display != reference_display
    ]
    day_positive = sum(value > 0 for value in day_values) / len(day_values) if day_values else 0.5
    breadth = max(0.0, min(100.0, 0.78 * fast_breadth + 0.22 * 100.0 * day_positive))
    signed = (
        0.46 * (structure - 50.0) * 2.0
        + 0.27 * (demand - 50.0) * 2.0
        + 0.27 * (breadth - 50.0) * 2.0
    )
    signed = max(-100.0, min(100.0, signed))
    color = _market_color(signed)
    direction = "▲" if signed >= 8.0 else ("▼" if signed <= -8.0 else "=")
    count = 0 if color == YELLOW else min(8, max(1, int(round(abs(signed) / 12.5))))
    reasons: list[str] = []
    if structure >= 65:
        reasons.append("BTC-Struktur stabil")
    elif structure <= 35:
        reasons.append("BTC-Struktur schwach")
    if positive_breadth >= 0.56:
        reasons.append("breite positive Nachfrage")
    if negative_breadth >= 0.45:
        reasons.append("breiter Verkaufsdruck")
    if btc.exact_interval_volume:
        reasons.append("BTC-1m-Volumen bestätigt")
    btc_reference_score = max(0.0, min(100.0, 0.58 * structure + 0.42 * demand))
    if btc_reference_score >= 78.0:
        btc_color = PURPLE
    elif btc_reference_score >= 62.0:
        btc_color = GREEN
    elif btc_reference_score >= 48.0:
        btc_color = BLUE
    elif btc_reference_score >= 36.0:
        btc_color = YELLOW
    elif btc_reference_score >= 24.0:
        btc_color = ORANGE
    else:
        btc_color = RED
    return MarketQuality(
        score=round(signed, 4),
        color=color,
        direction=direction,
        strength_count=count,
        btc_structure_score=round(structure, 4),
        btc_demand_score=round(demand, 4),
        breadth_score=round(breadth, 4),
        positive_breadth=round(positive_breadth, 5),
        negative_breadth=round(negative_breadth, 5),
        exact_volume=btc.exact_interval_volume,
        btc_reference_score=round(btc_reference_score, 4),
        btc_reference_color=btc_color,
        reasons=tuple(reasons),
    )


def _liquidity_score(current: Mapping[str, Any], metrics: IntradayMetrics) -> float:
    volume = max(0.0, float(current.get("volume") or metrics.quote_volume_24h or 0.0))
    cap = max(0.0, float(current.get("cap") or 0.0))
    absolute = _clamp((math.log10(max(volume, 1.0)) - 5.5) / 4.0)
    turnover = volume / cap if cap > 0 else 0.0
    turnover_score = _clamp(math.log10(1.0 + turnover * 100.0) / 1.7)
    execution = _clamp(float(metrics.execution_quality_score) / 100.0)
    return 100.0 * (0.52 * absolute + 0.23 * turnover_score + 0.25 * execution)


def _relative_strength_score(
    *, intraday: IntradayMetrics, btc_intraday: IntradayMetrics | None, short: ShortMetrics
) -> float:
    values: list[tuple[float, float]] = []
    if btc_intraday and btc_intraday.data_quality != "insufficient":
        for window, weight in ((15, 0.25), (30, 0.45), (60, 0.30)):
            coin = intraday.price_changes.get(window)
            btc = btc_intraday.price_changes.get(window)
            if coin is not None and btc is not None:
                score = 50.0 + max(-50.0, min(50.0, (float(coin) - float(btc)) * 18.0))
                values.append((score, weight))
    exact = (
        sum(score * weight for score, weight in values) / sum(weight for _, weight in values)
        if values else 50.0
    )
    color_component = 50.0 + color_level(short.relative_color) * 14.0
    return max(0.0, min(100.0, 0.76 * exact + 0.24 * color_component))


def _fallback_intraday(short: ShortMetrics, display: str) -> IntradayMetrics:
    stable = 100.0
    falling = False
    for window, limit in ((10, -0.18), (30, -0.30), (60, -0.55)):
        value = short.price_changes.get(window)
        if value is not None and float(value) < limit:
            stable -= 32.0
            falling = True
    positive_gap = short.divergence_score if short.flash_direction == "▲" else 0.0
    negative_gap = short.divergence_score if short.flash_direction == "▼" else 0.0
    return IntradayMetrics(
        display=display,
        provider="lcw-map-fallback",
        data_quality="partial" if short.data_quality != "insufficient" else "insufficient",
        exact_interval_volume=False,
        demand_score=positive_gap,
        sell_pressure_score=negative_gap,
        base_quality_score=max(0.0, stable),
        room_to_target_score=50.0,
        falling_knife=falling,
        late_entry=False,
        volume_colors=dict(short.volume_colors),
        reasons=("LCW-Map-Fallback",),
    )


def assess_opportunity(
    *,
    display: str,
    current: Mapping[str, Any],
    short: ShortMetrics,
    flash_signal: Any | None,
    intraday: IntradayMetrics | None,
    btc_intraday: IntradayMetrics | None,
    market_quality: MarketQuality,
    historical_target: Mapping[str, Any] | None,
    live_target: Mapping[str, Any] | None,
    unlock_penalty: float,
    category_context: Mapping[str, Any] | None,
    category_trend: Mapping[str, Any] | None,
    event_penalty: float,
    config: Mapping[str, Any],
) -> OpportunityAssessment:
    metrics = intraday if intraday and intraday.data_quality != "insufficient" else _fallback_intraday(short, display)
    exact = bool(metrics.exact_interval_volume)
    target_score, target_confidence = combine_target_profiles(historical_target, live_target)
    relative = _relative_strength_score(intraday=metrics, btc_intraday=btc_intraday, short=short)
    liquidity = _liquidity_score(current, metrics)

    flash_entry = float(getattr(flash_signal, "entry_score", 0.0)) if flash_signal else 0.0
    flash_exit = float(getattr(flash_signal, "exit_score", 0.0)) if flash_signal else 0.0
    exact_gap = float(metrics.demand_score) * (0.68 + 0.32 * _clamp(float(metrics.base_quality_score) / 100.0))
    positive_gap = max(0.32 * flash_entry + 0.68 * exact_gap, exact_gap * 0.86)
    negative_gap = max(flash_exit, float(metrics.sell_pressure_score), 0.30 * flash_exit + 0.70 * float(metrics.sell_pressure_score))

    category_context = category_context if isinstance(category_context, Mapping) else {}
    category_trend = category_trend if isinstance(category_trend, Mapping) else {}
    category_score = float(category_context.get("category_score", 50.0))
    category_color = str(category_context.get("category_color") or YELLOW)
    category_code = str(category_context.get("category_code") or "?")
    category_boost = float(category_context.get("category_boost", 0.0))
    laggard_score = float(category_context.get("laggard_score", 0.0))
    activity_score = float(category_context.get("activity_score", 0.0))
    category_fading = max(0.0, min(100.0, float(category_trend.get("fading_score", 0.0))))
    category_strengthening = max(0.0, min(100.0, float(category_trend.get("strengthening_score", 0.0))))
    category_trend_confidence = max(0.0, min(1.0, float(category_trend.get("confidence", 0.0))))
    bounded_event = min(float((config.get("event_risk") or {}).get("maximum_penalty", 12.0)), max(0.0, float(event_penalty)))
    bounded_unlock = min(float((config.get("unlock_risk") or {}).get("maximum_penalty", 20.0)), max(0.0, unlock_penalty))

    market_adjustment = max(-7.0, min(5.0, market_quality.score * 0.08))
    base = float(metrics.base_quality_score)
    room = float(metrics.room_to_target_score)
    demand = float(metrics.demand_score)
    spread = metrics.spread_pct
    fee_pct = float((config.get("execution") or {}).get("assumed_round_trip_fees_pct", 0.30))
    slippage_pct = float((config.get("execution") or {}).get("assumed_slippage_pct", 0.12))
    spread_cost = 0.0 if spread is None else max(0.0, float(spread))
    total_cost = fee_pct + slippage_pct + spread_cost
    net_target_quality = 100.0 * _clamp((3.0 - total_cost - 0.6) / 2.4)
    execution_quality = float(metrics.execution_quality_score)

    p5 = float(metrics.price_changes.get(5) or 0.0)
    p15 = float(metrics.price_changes.get(15) or 0.0)
    p30 = float(metrics.price_changes.get(30) or 0.0)
    entry_guard = config.get("entry_guard") if isinstance(config, Mapping) else {}
    entry_guard = entry_guard if isinstance(entry_guard, Mapping) else {}
    cheap_score = float(getattr(metrics, "cheap_price_score", 0.0))
    stabilization_score = float(getattr(metrics, "stabilization_score", 0.0))
    recent_drawdown = float(getattr(metrics, "recent_drawdown_pct", 0.0))
    rebound_from_low = float(getattr(metrics, "rebound_from_low_pct", 0.0))
    low_age = getattr(metrics, "new_3h_low_age_minutes", None)
    pos180 = float(metrics.range_position_180) if metrics.range_position_180 is not None else 1.0
    minimum_cheap = float(entry_guard.get("minimum_cheap_price_score", 48.0))
    minimum_stabilization = float(entry_guard.get("minimum_stabilization_score", 48.0))
    minimum_drawdown = float(entry_guard.get("minimum_recent_drawdown_pct", 0.32))
    maximum_rebound = float(entry_guard.get("maximum_rebound_from_low_pct", 4.2))
    minimum_low_age = float(entry_guard.get("minimum_low_age_minutes", 4.0))
    maximum_low_age = float(entry_guard.get("maximum_low_age_minutes", 180.0))
    maximum_range_position = float(entry_guard.get("maximum_3h_range_position", 0.68))
    minimum_demand = float(entry_guard.get("minimum_demand_score", 50.0))
    minimum_v5 = float(entry_guard.get("minimum_5m_volume_ratio", 1.01))
    minimum_v15 = float(entry_guard.get("minimum_15m_volume_ratio", 1.01))
    v5 = metrics.volume_ratios.get(5)
    v15 = metrics.volume_ratios.get(15)
    a5 = metrics.volume_acceleration.get(5)
    a15 = metrics.volume_acceleration.get(15)
    volume_confirmed = bool(
        (v5 is not None and float(v5) >= minimum_v5)
        or (v15 is not None and float(v15) >= minimum_v15)
        or (a5 is not None and float(a5) >= 1.04)
        or (a15 is not None and float(a15) >= 1.03)
    )
    demand_confirmed = bool(demand >= minimum_demand and volume_confirmed)

    base_discount = bool(
        exact
        and cheap_score >= minimum_cheap
        and recent_drawdown >= minimum_drawdown
        and pos180 <= maximum_range_position
    )
    category_assisted_discount = bool(
        exact
        and category_score >= float(entry_guard.get("category_assist_minimum_score", 62.0))
        and laggard_score >= float(entry_guard.get("category_assist_minimum_laggard", 18.0))
        and cheap_score >= float(entry_guard.get("category_assist_minimum_cheap_score", 45.0))
        and (
            recent_drawdown >= float(entry_guard.get("category_assist_minimum_drawdown_pct", 0.25))
            or pos180 <= 0.50
        )
        and pos180 <= float(entry_guard.get("category_assist_maximum_3h_range_position", 0.70))
    )
    discount_qualified = bool(base_discount or category_assisted_discount)
    stabilized_after_drop = bool(
        discount_qualified
        and stabilization_score >= minimum_stabilization
        and low_age is not None
        and minimum_low_age <= float(low_age) <= maximum_low_age
        and rebound_from_low <= maximum_rebound
        and p5 >= float(entry_guard.get("minimum_5m_price_pct", -0.12))
        and p15 >= float(entry_guard.get("minimum_15m_price_pct", -0.28))
        and p30 >= float(entry_guard.get("minimum_30m_price_pct", -0.55))
        and not metrics.falling_knife
    )
    # A controlled blue-stage entry is intentionally broader than the fully
    # confirmed recovery.  It still requires a real discount/laggard position,
    # a held low and renewed one-minute demand, but it must not be invalidated
    # later by reapplying the stricter green gate.
    early_entry_ready = bool(
        exact
        and not metrics.falling_knife
        and not metrics.late_entry
        and low_age is not None
        and 3.0 <= float(low_age) <= 210.0
        and rebound_from_low <= 4.8
        and p5 >= -0.18
        and p15 >= -0.38
        and p30 >= -0.72
        and demand >= 47.0
        and volume_confirmed
        and (
            (
                cheap_score >= 42.0
                and recent_drawdown >= 0.20
                and pos180 <= 0.72
                and stabilization_score >= 40.0
            )
            or (
                category_score >= 48.0
                and laggard_score >= 12.0
                and cheap_score >= 39.0
                and stabilization_score >= 38.0
                and (recent_drawdown >= 0.15 or pos180 <= 0.58)
                and pos180 <= 0.74
            )
        )
    )

    confirmed_v5 = float(entry_guard.get("confirmed_minimum_5m_volume_ratio", 1.03))
    confirmed_v15 = float(entry_guard.get("confirmed_minimum_15m_volume_ratio", 1.02))
    confirmed_volume = bool(
        (v5 is not None and float(v5) >= confirmed_v5)
        or (v15 is not None and float(v15) >= confirmed_v15)
        or (a5 is not None and float(a5) >= 1.06)
        or (a15 is not None and float(a15) >= 1.04)
    )
    confirmed_demand = bool(
        demand >= float(entry_guard.get("confirmed_minimum_demand_score", 55.0))
        and confirmed_volume
    )
    confirmed_recovery = bool(
        exact
        and cheap_score >= float(entry_guard.get("confirmed_minimum_cheap_price_score", 58.0))
        and recent_drawdown >= float(entry_guard.get("confirmed_minimum_recent_drawdown_pct", 0.65))
        and pos180 <= float(entry_guard.get("confirmed_maximum_3h_range_position", 0.60))
        and stabilization_score >= float(entry_guard.get("confirmed_minimum_stabilization_score", 60.0))
        and low_age is not None
        and float(entry_guard.get("confirmed_minimum_low_age_minutes", 8.0)) <= float(low_age) <= float(entry_guard.get("confirmed_maximum_low_age_minutes", 150.0))
        and rebound_from_low <= float(entry_guard.get("confirmed_maximum_rebound_from_low_pct", 3.8))
        and p5 >= float(entry_guard.get("confirmed_minimum_5m_price_pct", -0.08))
        and p15 >= float(entry_guard.get("confirmed_minimum_15m_price_pct", -0.20))
        and p30 >= float(entry_guard.get("confirmed_minimum_30m_price_pct", -0.42))
        and confirmed_demand
        and not metrics.falling_knife
    )
    category_lead = 0.0
    if category_strengthening >= 25.0 and laggard_score >= 15.0 and p15 <= 0.9 and p30 <= 1.5:
        category_lead = min(12.0, 0.075 * category_strengthening + 0.11 * laggard_score)

    weights = (config.get("opportunity_score") or {}).get("entry_weights", {})
    def weight(name: str, fallback: float) -> float:
        try:
            return max(0.0, float(weights.get(name, fallback)))
        except (TypeError, ValueError):
            return fallback
    raw_entry = (
        weight("current_demand", 0.22) * demand
        + weight("three_hour_base", 0.17) * base
        + weight("volume_price_gap", 0.12) * positive_gap
        + weight("relative_strength", 0.05) * relative
        + weight("room_to_target", 0.10) * room
        + weight("target_history", 0.10) * target_score
        + weight("liquidity", 0.05) * liquidity
        + weight("category_strength", 0.09) * category_score
        + weight("category_laggard", 0.07) * laggard_score
        + weight("recent_activity", 0.03) * activity_score
    )
    recovery_bonus_cap = float(entry_guard.get("recovery_setup_bonus_cap", 10.0))
    early_bonus_cap = float(entry_guard.get("early_recovery_bonus_cap", 3.5))
    recovery_setup_bonus = 0.0
    recovery_quality = (
        0.38 * _clamp(cheap_score / 100.0)
        + 0.38 * _clamp(stabilization_score / 100.0)
        + 0.24 * _clamp(demand / 100.0)
    )
    if confirmed_recovery:
        recovery_setup_bonus = min(recovery_bonus_cap, recovery_bonus_cap * recovery_quality)
    elif discount_qualified and stabilized_after_drop and demand_confirmed:
        recovery_setup_bonus = min(early_bonus_cap, early_bonus_cap * recovery_quality)
    falling_penalty = 84.0 if metrics.falling_knife else 0.0
    late_penalty = 0.48 * float(metrics.overextension_penalty)
    spread_penalty = 0.0 if spread is None else max(0.0, (float(spread) - 0.20) * 24.0)
    entry = raw_entry + recovery_setup_bonus + market_adjustment + category_boost + category_lead - falling_penalty - late_penalty - bounded_unlock - bounded_event - spread_penalty
    data_confidence = {"good": 1.0, "partial": 0.78, "insufficient": 0.42}.get(metrics.data_quality, 0.42)
    if not exact:
        data_confidence = min(data_confidence, 0.68)
    entry = max(0.0, min(100.0, entry)) * (0.70 + 0.30 * data_confidence)
    entry *= 0.82 + 0.18 * _clamp(execution_quality / 100.0)
    if metrics.falling_knife:
        entry = min(entry, 8.0)
    if metrics.late_entry:
        entry = min(entry, 50.0)
    # Activity alone remains insufficient.  A real discount, a beginning hold
    # and renewed demand unlock blue.  Until the stricter recovery is complete,
    # the score is capped below green so early setups cannot masquerade as fully
    # confirmed entries.
    entry_thresholds = config.get("opportunity_score") or {}
    if not early_entry_ready:
        entry = min(entry, float(entry_thresholds.get("entry_blue", 38.0)) - 0.25)
    elif not confirmed_recovery:
        entry = min(entry, float(entry_thresholds.get("entry_green", 60.0)) - 0.25)

    p60 = float(metrics.price_changes.get(60) or 0.0)
    p180 = float(metrics.price_changes.get(180) or 0.0)
    range_position = float(metrics.range_position_180) if metrics.range_position_180 is not None else 0.50
    elevated = max(
        float(metrics.overextension_penalty),
        100.0 * _clamp((range_position - 0.70) / 0.30),
        100.0 * _clamp((p180 - 1.50) / 5.50),
    )
    reversal = max(
        100.0 if metrics.falling_knife else 100.0 * _clamp((-p60 - 0.25) / 1.8),
        100.0 * _clamp((0.50 - float(metrics.taker_buy_share.get(30) or 0.50)) / 0.14),
    )
    missing_support = max(negative_gap, float(metrics.sell_pressure_score))
    exit_raw = (
        0.31 * missing_support
        + 0.25 * elevated
        + 0.20 * category_fading
        + 0.14 * reversal
        + 0.10 * (100.0 * data_confidence)
    )
    v30 = metrics.volume_ratios.get(30)
    unsupported_floor = 0.0
    if p30 >= 0.35 and v30 is not None and float(v30) <= 0.82:
        unsupported_floor = 44.0 + min(24.0, (p30 - 0.35) * 7.0 + (0.82 - float(v30)) * 55.0)
        unsupported_floor += min(12.0, category_fading * 0.12)
    exit_score = max(0.0, min(100.0, max(exit_raw, unsupported_floor))) * (0.76 + 0.24 * data_confidence)
    if metrics.falling_knife:
        exit_score = max(exit_score, 60.0 + min(26.0, abs(p60) * 9.0))
    entry = max(0.0, entry - max(0.0, exit_score - 34.0) * 0.24)

    thresholds = config.get("opportunity_score") if isinstance(config, Mapping) else {}
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    entry_blue = float(thresholds.get("entry_blue", 38.0))
    exit_orange = float(thresholds.get("exit_orange", 48.0))
    max_exit = float(thresholds.get("maximum_exit_risk_for_display", 62.0))
    category_rules = config.get("category_rotation") if isinstance(config, Mapping) else {}
    category_rules = category_rules if isinstance(category_rules, Mapping) else {}
    minimum_category = float(category_rules.get("minimum_category_score", 36.0))
    weak_exception = float(category_rules.get("weak_category_exception_entry", 58.0))
    max_slots = int(category_context.get("max_slots", 0))
    spread_block = spread is not None and float(spread) >= float((config.get("execution") or {}).get("maximum_spread_pct", 1.0))

    qualified_entry = (
        max_slots > 0
        and (category_score >= minimum_category or entry >= weak_exception)
        and entry >= entry_blue
        and exit_score <= max_exit
        and not metrics.falling_knife
        and not metrics.late_entry
        and not spread_block
        and early_entry_ready
        and base >= 34.0
        and room >= 24.0
        and net_target_quality >= 35.0
        and data_confidence >= 0.55
    )
    fading_confirmed = category_fading >= float(thresholds.get("minimum_category_fading_for_exit", 28.0))
    elevated_without_demand = elevated >= 28.0 and demand <= 58.0 and missing_support >= 34.0
    qualified_exit = (
        exit_score >= exit_orange
        and fading_confirmed
        and elevated_without_demand
        and category_trend_confidence >= float(thresholds.get("minimum_category_trend_confidence", 0.34))
        and data_confidence >= 0.55
    )
    if qualified_entry and qualified_exit:
        if exit_score >= entry + 7.0:
            qualified_entry = False
        else:
            qualified_exit = False

    if qualified_entry:
        direction, color = "▲", BLUE
        ranking = entry + 3.0 * data_confidence + category_lead
    elif qualified_exit:
        direction, color = "▼", ORANGE
        ranking = exit_score + 2.5 * data_confidence + min(5.0, category_fading * 0.05)
    else:
        direction = "▲" if entry >= exit_score else "▼"
        color = BLUE if direction == "▲" else ORANGE
        ranking = max(entry, exit_score)
    count = min(8, max(1, int(round(max(entry, exit_score) / 12.5))))

    reasons: list[str] = list(metrics.reasons)
    reasons.append(f"Kategorie {category_code} {category_score:.0f}")
    if category_lead >= 4.0:
        reasons.append("Kategorie führt, Coin zieht nach")
    if discount_qualified:
        reasons.append("Preis günstig nach Rücklauf")
    if early_entry_ready and not stabilized_after_drop:
        reasons.append("früher stabiler Kategorie-Nachzügler")
    if stabilized_after_drop:
        reasons.append("Stabilisierung beginnt")
    if confirmed_recovery:
        reasons.append("Erholung vollständig bestätigt")
    if demand_confirmed:
        reasons.append("Nachfrage nach Stabilisierung bestätigt")
    if not discount_qualified:
        reasons.append("kein ausreichender Preisabschlag")
    elif not stabilized_after_drop:
        reasons.append("Stabilisierung noch nicht ausreichend")
    elif not demand_confirmed:
        reasons.append("Nachfragebestätigung fehlt")
    if target_confidence >= 0.35:
        reasons.append(f"3/5%-Historie {target_score:.0f}")
    if total_cost >= 0.8:
        reasons.append("Handelskosten erhöht")
    if bounded_unlock >= 8:
        reasons.append("Unlock-Abzug")
    if bounded_event >= 7:
        reasons.append("Ereignis-Abzug")

    visible_colors = {window: metrics.volume_colors.get(window, short.volume_colors.get(window, WHITE)) for window in (10, 30, 60)}
    return OpportunityAssessment(
        display=display,
        entry_score=round(entry, 4),
        exit_score=round(exit_score, 4),
        ranking_score=round(ranking, 4),
        direction=direction,
        color=color,
        strength_count=count,
        demand_score=round(demand, 4),
        base_quality_score=round(base, 4),
        cheap_price_score=round(cheap_score, 4),
        stabilization_score=round(stabilization_score, 4),
        recent_drawdown_pct=round(recent_drawdown, 5),
        rebound_from_low_pct=round(rebound_from_low, 5),
        discount_qualified=discount_qualified,
        stabilized_after_drop=stabilized_after_drop,
        demand_confirmed=demand_confirmed,
        confirmed_recovery=confirmed_recovery,
        positive_gap_score=round(positive_gap, 4),
        negative_gap_score=round(negative_gap, 4),
        relative_strength_score=round(relative, 4),
        room_to_target_score=round(room, 4),
        target_prior_score=round(target_score, 4),
        target_prior_confidence=round(target_confidence, 4),
        liquidity_score=round(liquidity, 4),
        execution_quality_score=round(execution_quality, 4),
        spread_pct=None if spread is None else round(float(spread), 5),
        estimated_round_trip_cost_pct=round(total_cost, 5),
        net_target_quality_score=round(net_target_quality, 4),
        category_lead_bonus=round(category_lead, 4),
        market_adjustment=round(market_adjustment, 4),
        unlock_penalty=round(bounded_unlock, 4),
        late_entry_penalty=round(late_penalty, 4),
        falling_knife_penalty=round(falling_penalty, 4),
        exact_volume=exact,
        provider=metrics.provider,
        provider_symbol=metrics.symbol,
        data_confidence=round(data_confidence, 4),
        falling_knife=metrics.falling_knife,
        late_entry=metrics.late_entry,
        qualified_entry=qualified_entry,
        qualified_exit=qualified_exit,
        category_code=category_code,
        category_score=round(category_score, 4),
        category_color=category_color,
        category_boost=round(category_boost, 4),
        laggard_score=round(laggard_score, 4),
        activity_score=round(activity_score, 4),
        event_penalty=round(bounded_event, 4),
        category_fading_score=round(category_fading, 4),
        category_strengthening_score=round(category_strengthening, 4),
        category_trend_confidence=round(category_trend_confidence, 4),
        volume_colors=visible_colors,
        reasons=tuple(dict.fromkeys(reasons)),
    )
# Package revision: v3.5.0-buy-gate-fix-r5
