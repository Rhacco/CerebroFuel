"""Entry-opportunity, exit-warning and market-regime scoring for v3.3.3."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

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
    positive_gap_score: float
    negative_gap_score: float
    relative_strength_score: float
    room_to_target_score: float
    target_prior_score: float
    target_prior_confidence: float
    liquidity_score: float
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
    flash_signals: Mapping[str, Any],
    rows_by_display: Mapping[str, Mapping[str, Any]],
    reference_display: str,
) -> MarketQuality:
    btc = btc_intraday or IntradayMetrics(display=reference_display)
    p15 = float(btc.price_changes.get(15) or 0.0)
    p60 = float(btc.price_changes.get(60) or 0.0)
    p180 = float(btc.price_changes.get(180) or 0.0)
    structure = 50.0
    structure += max(-28.0, min(28.0, p15 * 16.0))
    structure += max(-25.0, min(25.0, p60 * 8.0))
    structure += max(-20.0, min(20.0, p180 * 3.5))
    if btc.falling_knife:
        structure -= 35.0
    if btc.late_entry:
        structure -= 8.0
    structure = max(0.0, min(100.0, structure))

    demand = float(btc.demand_score if btc.exact_interval_volume else 50.0)
    demand -= 0.55 * float(btc.sell_pressure_score)
    demand = max(0.0, min(100.0, demand))

    positives = negatives = usable = 0
    for display, signal in flash_signals.items():
        if display == reference_display:
            continue
        score = float(getattr(signal, "score", 0.0))
        direction = str(getattr(signal, "direction", "="))
        quality = float(getattr(signal, "quality", 0.0))
        if quality < 0.45 or score < 18.0:
            continue
        usable += 1
        positives += direction == "▲"
        negatives += direction == "▼"
    positive_breadth = positives / usable if usable else 0.0
    negative_breadth = negatives / usable if usable else 0.0
    flash_breadth = 50.0 + 75.0 * (positive_breadth - negative_breadth)

    day_values: list[float] = []
    for display, row in rows_by_display.items():
        if display == reference_display:
            continue
        day_values.append(_delta_pct((row.get("delta") or {}).get("day")))
    day_positive = sum(value > 0 for value in day_values) / len(day_values) if day_values else 0.5
    day_breadth = 100.0 * day_positive
    breadth = max(0.0, min(100.0, 0.68 * flash_breadth + 0.32 * day_breadth))

    signed = (
        0.46 * (structure - 50.0) * 2.0
        + 0.24 * (demand - 50.0) * 2.0
        + 0.30 * (breadth - 50.0) * 2.0
    )
    signed = max(-100.0, min(100.0, signed))
    color = _market_color(signed)
    direction = "▲" if signed >= 8.0 else ("▼" if signed <= -8.0 else "=")
    count = 0 if color == YELLOW else min(8, max(2, int(round(abs(signed) / 12.5))))
    reasons: list[str] = []
    if structure >= 65:
        reasons.append("BTC-Struktur stabil")
    elif structure <= 35:
        reasons.append("BTC-Struktur schwach")
    if positive_breadth >= 0.58:
        reasons.append("breite positive Coin-Nachfrage")
    if negative_breadth >= 0.48:
        reasons.append("breiter Verkaufsdruck")
    if btc.exact_interval_volume:
        reasons.append("BTC-Intervallvolumen bestätigt")
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
        reasons=tuple(reasons),
    )


def _liquidity_score(current: Mapping[str, Any]) -> float:
    volume = max(0.0, float(current.get("volume") or 0.0))
    cap = max(0.0, float(current.get("cap") or 0.0))
    absolute = _clamp((math.log10(max(volume, 1.0)) - 5.5) / 4.0)
    turnover = volume / cap if cap > 0 else 0.0
    turnover_score = _clamp(math.log10(1.0 + turnover * 100.0) / 1.7)
    return 100.0 * (0.68 * absolute + 0.32 * turnover_score)


def _relative_strength_score(
    *,
    intraday: IntradayMetrics | None,
    btc_intraday: IntradayMetrics | None,
    short: ShortMetrics,
) -> float:
    values: list[float] = []
    if intraday and btc_intraday and intraday.data_quality != "insufficient" and btc_intraday.data_quality != "insufficient":
        for window, weight in ((30, 0.60), (60, 0.40)):
            coin = intraday.price_changes.get(window)
            btc = btc_intraday.price_changes.get(window)
            if coin is not None and btc is not None:
                values.append(50.0 + max(-50.0, min(50.0, (float(coin) - float(btc)) * 18.0)) * weight)
    if values:
        exact = sum(values) / len(values)
    else:
        exact = 50.0
    color_component = 50.0 + color_level(short.relative_color) * 14.0
    return max(0.0, min(100.0, 0.68 * exact + 0.32 * color_component))


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
        provider="lcw-rolling-fallback",
        data_quality="partial" if short.data_quality != "insufficient" else "insufficient",
        exact_interval_volume=False,
        demand_score=positive_gap,
        sell_pressure_score=negative_gap,
        base_quality_score=max(0.0, stable),
        room_to_target_score=50.0,
        falling_knife=falling,
        late_entry=False,
        volume_colors=dict(short.volume_colors),
        reasons=("LCW-Rolling-Volumen-Fallback",),
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
    config: Mapping[str, Any],
) -> OpportunityAssessment:
    metrics = intraday if intraday and intraday.data_quality != "insufficient" else _fallback_intraday(short, display)
    exact = bool(metrics.exact_interval_volume)
    target_score, target_confidence = combine_target_profiles(historical_target, live_target)
    relative = _relative_strength_score(intraday=metrics, btc_intraday=btc_intraday, short=short)
    liquidity = _liquidity_score(current)

    flash_entry = float(getattr(flash_signal, "entry_score", 0.0)) if flash_signal else 0.0
    flash_exit = float(getattr(flash_signal, "exit_score", 0.0)) if flash_signal else 0.0
    detailed_entry = float(short.divergence_score) if short.flash_direction == "▲" else 0.0
    detailed_exit = float(short.divergence_score) if short.flash_direction == "▼" else 0.0
    exact_gap = float(metrics.demand_score) * (
        0.68 + 0.32 * _clamp(float(metrics.base_quality_score) / 100.0)
    )
    positive_gap = max(0.48 * max(flash_entry, detailed_entry) + 0.52 * exact_gap, exact_gap * 0.82)
    legacy_negative = max(flash_exit, detailed_exit)
    negative_gap = max(
        legacy_negative,
        0.45 * legacy_negative + 0.55 * float(metrics.sell_pressure_score),
        float(metrics.sell_pressure_score) * 0.90,
    )

    market_adjustment = max(-12.0, min(8.0, market_quality.score * 0.12))
    base = float(metrics.base_quality_score)
    room = float(metrics.room_to_target_score)
    demand = float(metrics.demand_score)
    raw_entry = (
        0.28 * demand
        + 0.20 * base
        + 0.15 * positive_gap
        + 0.10 * relative
        + 0.10 * room
        + 0.12 * target_score
        + 0.05 * liquidity
    )

    falling_penalty = 78.0 if metrics.falling_knife else 0.0
    late_penalty = 0.42 * float(metrics.overextension_penalty)
    bounded_unlock = min(float(config.get("unlock_risk", {}).get("maximum_penalty", 20.0)), max(0.0, unlock_penalty))
    data_confidence = {
        "good": 1.0,
        "partial": 0.78,
        "insufficient": 0.45,
    }.get(metrics.data_quality, 0.45)
    if not exact:
        data_confidence = min(data_confidence, 0.72)
    entry = raw_entry + market_adjustment - falling_penalty - late_penalty - bounded_unlock
    entry = max(0.0, min(100.0, entry))
    entry *= 0.72 + 0.28 * data_confidence
    if metrics.falling_knife:
        entry = min(entry, 14.0)
    if metrics.late_entry:
        entry = min(entry, 58.0)

    p60 = float(metrics.price_changes.get(60) or 0.0)
    p180 = float(metrics.price_changes.get(180) or 0.0)
    base_break = 100.0 if metrics.falling_knife else 100.0 * _clamp((-p60 - 0.25) / 1.8)
    failed_run = max(float(metrics.overextension_penalty), 100.0 * _clamp((p180 - 2.0) / 7.0) * _clamp((55.0 - demand) / 40.0))
    market_negative = max(0.0, -market_quality.score)
    exit_raw = (
        0.44 * negative_gap
        + 0.18 * float(metrics.sell_pressure_score)
        + 0.15 * base_break
        + 0.09 * failed_run
        + 0.08 * market_negative
        + 0.06 * min(100.0, bounded_unlock * 5.0)
    )
    # A visibly rising price with clearly shrinking/lagging volume is the
    # canonical unsupported-run warning.  Preserve that signal even when the
    # broader base has not broken yet.
    p30 = float(metrics.price_changes.get(30) or 0.0)
    v30 = metrics.volume_ratios.get(30)
    unsupported_run_floor = 0.0
    if p30 >= 0.35 and v30 is not None and float(v30) <= 0.82:
        unsupported_run_floor = 44.0 + min(24.0, (p30 - 0.35) * 7.0 + (0.82 - float(v30)) * 55.0)
    exit_score = max(0.0, min(100.0, max(exit_raw, unsupported_run_floor))) * (0.76 + 0.24 * data_confidence)
    if metrics.falling_knife:
        exit_score = max(exit_score, 58.0 + min(28.0, abs(p60) * 10.0))

    thresholds = config.get("opportunity_score") if isinstance(config, Mapping) else None
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    entry_blue = float(thresholds.get("entry_blue", 42.0))
    entry_green = float(thresholds.get("entry_green", 62.0))
    entry_purple = float(thresholds.get("entry_purple", 82.0))
    exit_orange = float(thresholds.get("exit_orange", 44.0))
    exit_red = float(thresholds.get("exit_red", 70.0))

    if entry >= exit_score + 2.0:
        direction = "▲"
        dominant = entry
        if entry >= entry_purple and exact and base >= 62.0 and not metrics.late_entry:
            color = PURPLE
        elif entry >= entry_green:
            color = GREEN
        elif entry >= entry_blue:
            color = BLUE
        else:
            color = YELLOW
    elif exit_score >= entry + 2.0:
        direction = "▼"
        dominant = exit_score
        color = RED if exit_score >= exit_red else (ORANGE if exit_score >= exit_orange else YELLOW)
    else:
        direction = "▲" if entry >= exit_score else "▼"
        dominant = max(entry, exit_score)
        color = YELLOW

    count = 0 if color == YELLOW else min(8, max(2, int(round(dominant / 12.5))))
    ranking = dominant + 3.0 * data_confidence
    if color == YELLOW:
        ranking *= 0.72

    reasons: list[str] = list(metrics.reasons)
    if entry >= entry_green:
        reasons.append(f"Entry {entry:.0f}")
    if exit_score >= exit_orange:
        reasons.append(f"Exit-Risiko {exit_score:.0f}")
    if target_confidence >= 0.35:
        reasons.append(f"3/5%-Historie {target_score:.0f}")
    if market_adjustment <= -6:
        reasons.append("schwacher Gesamtmarkt")
    if bounded_unlock >= 8:
        reasons.append("Unlock-Abzug")

    visible_colors = {
        window: metrics.volume_colors.get(window, short.volume_colors.get(window, WHITE))
        for window in (10, 30, 60)
    }
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
        positive_gap_score=round(positive_gap, 4),
        negative_gap_score=round(negative_gap, 4),
        relative_strength_score=round(relative, 4),
        room_to_target_score=round(room, 4),
        target_prior_score=round(target_score, 4),
        target_prior_confidence=round(target_confidence, 4),
        liquidity_score=round(liquidity, 4),
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
        volume_colors=visible_colors,
        reasons=tuple(dict.fromkeys(reasons)),
    )
