"""Deterministic short-term crypto anomaly analysis for Discord (v3.2.3)."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

DAY_NAMES = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
# Display selected weekdays chronologically, starting with Saturday.
DISPLAY_WEEK_ORDER = (5, 6, 0, 1, 2, 3, 4)
WINDOWS = (10, 20, 60)

PURPLE = "🟣"
GREEN = "🟢"
BLUE = "🔵"
YELLOW = "🟡"
ORANGE = "🟠"
RED = "🔴"
BROWN = "🟤"
WHITE = "⚪"
BLACK = "⚫"


@dataclass(frozen=True)
class PricePoint:
    timestamp_ms: int
    rate: float
    volume: float | None


@dataclass
class Seasonality:
    current: str
    best_weekdays: tuple[str, ...]
    samples: int
    source: str
    current_score: float | None = None
    current_confidence: float = 0.0
    weekday_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class ShortMetrics:
    price_changes: dict[int, float | None]
    volume_changes: dict[int, float | None]
    volume_colors: dict[int, str]
    relative_short_pct: float | None
    relative_color: str
    pressure_score: float | None
    pressure_color: str
    buy_count: int
    sell_count: int
    direction: str
    signal_color: str
    anomaly_score: float
    data_quality: str
    window_quality: dict[int, str] = field(default_factory=dict)
    quality_reasons: dict[int, str] = field(default_factory=dict)
    window_setup_scores: dict[int, float | None] = field(default_factory=dict)
    agreement_score: float = 0.0



@dataclass
class CoinAnalysis:
    display_code: str
    api_code: str
    price: float
    week_pct: float
    week_color: str
    short: ShortMetrics
    seasonality: Seasonality
    now_score: float | None = None
    now_color: str = YELLOW
    is_reference: bool = False
    btc_gate: bool = False


def delta_to_pct(value: Any) -> float:
    """LCW delta values are multipliers: 1.08 means +8%."""
    try:
        return (float(value) - 1.0) * 100.0
    except (TypeError, ValueError):
        return 0.0


def normalize_history(raw: Iterable[dict[str, Any]]) -> list[PricePoint]:
    by_timestamp: dict[int, PricePoint] = {}
    for row in raw:
        try:
            timestamp = int(row["date"])
            rate = float(row["rate"])
            if rate <= 0 or not math.isfinite(rate):
                continue
            raw_volume = row.get("volume")
            volume = float(raw_volume) if raw_volume not in (None, "") else None
            if volume is not None and (volume < 0 or not math.isfinite(volume)):
                volume = None
        except (KeyError, TypeError, ValueError):
            continue
        by_timestamp[timestamp] = PricePoint(timestamp, rate, volume)
    return sorted(by_timestamp.values(), key=lambda point: point.timestamp_ms)


CODE_ALIASES = {
    "NEAR": "NER",
    "HBAR": "HBR",
    "DOGE": "DGE",
    "RENDER": "RND",
    "ZKSYNC": "ZKS",
    "ETHFI": "EFI",
    "MORPHO": "MRP",
    "FARTCOIN": "FRT",
    "TRUMP": "TRP",
    "MEGA": "MEG",
    "KMNO": "KMN",
    "PYTH": "PYT",
    "AAVE": "AAV",
    "ONDO": "OND",
    "WLFI": "WLF",
    "HYPE": "HYP",
    "BONK": "BNK",
    "PEPE": "PEP",
    "PUMP": "PMP",
}


def abbreviate_code(code: str) -> str:
    """Return a stable, readable code of at most three characters."""
    cleaned = "".join(char for char in code.upper() if char.isalnum())
    if cleaned in CODE_ALIASES:
        return CODE_ALIASES[cleaned]
    if len(cleaned) <= 3:
        return cleaned
    return cleaned[:3]


def display_code(code: str) -> str:
    """Pad short codes with three spaces for every missing character."""
    short = abbreviate_code(code)
    missing = max(0, 3 - len(short))
    return short + (" " * (3 * missing))



def _thresholds(config: Mapping[str, Any], name: str, window: int) -> tuple[float, float, float]:
    raw = config.get(name, {}) if isinstance(config, Mapping) else {}
    item = raw.get(str(window), {}) if isinstance(raw, Mapping) else {}
    light = float(item.get("light", 0.10))
    clear = float(item.get("clear", 0.35))
    strong = float(item.get("strong", 1.20))
    if not (0 <= light <= clear <= strong):
        raise ValueError(f"Ungültige Schwellen für {name}/{window}.")
    return light, clear, strong


def signed_color(
    value: float | None,
    *,
    light: float,
    clear: float,
    strong: float,
    uncertain: bool = False,
) -> str:
    if value is None or not math.isfinite(value):
        return WHITE
    if uncertain:
        return BROWN
    if value >= strong:
        return PURPLE
    if value >= clear:
        return GREEN
    if value >= light:
        return BLUE
    if value <= -clear:
        return RED
    if value <= -light:
        return ORANGE
    return YELLOW


def color_level(color: str) -> int:
    return {
        PURPLE: 3,
        GREEN: 2,
        BLUE: 1,
        YELLOW: 0,
        ORANGE: -1,
        RED: -2,
        BROWN: 0,
        WHITE: 0,
        BLACK: 0,
    }.get(color, 0)


def _pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _nearest_point(
    points: list[PricePoint], target_ms: int, max_distance_ms: int
) -> PricePoint | None:
    if not points:
        return None
    best = min(points, key=lambda point: abs(point.timestamp_ms - target_ms))
    if abs(best.timestamp_ms - target_ms) > max_distance_ms:
        return None
    return best


def compute_window_changes_from_history(
    *,
    current_rate: float,
    current_volume: float | None,
    history: list[PricePoint],
    now_ms: int,
) -> tuple[dict[int, float | None], dict[int, float | None]]:
    price_changes: dict[int, float | None] = {}
    volume_changes: dict[int, float | None] = {}
    usable = [point for point in history if point.timestamp_ms <= now_ms + 60_000]
    for window in WINDOWS:
        target_ms = now_ms - window * 60_000
        tolerance_minutes = max(8.0, window * 0.35)
        point = _nearest_point(usable, target_ms, int(tolerance_minutes * 60_000))
        price_changes[window] = _pct(current_rate, point.rate if point else None)
        volume_changes[window] = _pct(current_volume, point.volume if point else None)
    return price_changes, volume_changes


def _weighted_relative(
    price_changes: Mapping[int, float | None],
    btc_price_changes: Mapping[int, float | None],
) -> float | None:
    weights = {10: 0.45, 20: 0.35, 60: 0.20}
    values: list[tuple[float, float]] = []
    for window, weight in weights.items():
        coin_value = price_changes.get(window)
        btc_value = btc_price_changes.get(window)
        if coin_value is None or btc_value is None:
            continue
        values.append((coin_value - btc_value, weight))
    if len(values) < 2:
        return None
    total_weight = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / total_weight


def _relationship_score(price_level: float, volume_level: float) -> float:
    """Score the price/volume relationship on an approximate -3..+3 scale.

    Accumulation is rewarded early: stable/slightly rising price with a much
    stronger volume trend. Distribution is penalized early: rising price whose
    volume trend lags clearly behind, or falling price on rising volume.
    """
    # Stable price with rapidly rising volume: strong accumulation before breakout.
    if abs(price_level) < 0.55:
        if volume_level >= 2.0:
            return min(3.2, 2.45 + 0.35 * (volume_level - 2.0))
        if volume_level >= 1.0:
            return 1.35 + 0.35 * volume_level
        if volume_level <= -2.0:
            return -1.55
        if volume_level <= -1.0:
            return -0.85
        return 0.0

    if price_level > 0:
        gap = price_level - volume_level
        lead = volume_level - price_level
        # Volume leads price: accumulation / early demand.
        if volume_level >= 1.0 and lead >= 0.75:
            return min(3.2, 1.55 + 0.55 * volume_level + 0.20 * price_level)
        # Price outruns volume: bearish divergence / distribution warning.
        if price_level >= 1.0 and gap >= 1.25:
            return max(-3.2, -1.45 - 0.55 * gap - 0.20 * price_level)
        if price_level >= 1.0 and gap >= 0.70:
            return -1.05 - 0.35 * gap
        if volume_level >= 1.0:
            return min(3.0, 0.58 * price_level + 0.42 * volume_level)
        if volume_level <= -1.0:
            return max(-2.8, -0.85 - 0.35 * price_level - 0.45 * abs(volume_level))
        return 0.52 * price_level

    # Falling price with rising volume is confirmed selling pressure.
    if volume_level >= 2.0:
        return max(-3.2, 0.60 * price_level - 0.65 * volume_level)
    if volume_level >= 1.0:
        return max(-3.0, 0.65 * price_level - 0.50 * volume_level)
    # Price and volume both fall: demand is fading, still negative but less urgent.
    if volume_level <= -1.0:
        return max(-2.6, 0.62 * price_level - 0.22 * abs(volume_level))
    return 0.72 * price_level


def _window_setup_score(
    price_change: float | None,
    volume_change: float | None,
    *,
    window: int,
    config: Mapping[str, Any],
    quality: str,
) -> float | None:
    if quality != "good" or price_change is None or volume_change is None:
        return None
    p_light, p_clear, p_strong = _thresholds(config, "price", window)
    v_light, v_clear, v_strong = _thresholds(config, "volume", window)
    p = _normalized_signal_value(price_change, light=p_light, clear=p_clear, strong=p_strong)
    v = _normalized_signal_value(volume_change, light=v_light, clear=v_clear, strong=v_strong)
    return _relationship_score(p, v)


def _agreement_score(scores: Mapping[int, float | None]) -> float:
    values = [value for value in scores.values() if value is not None]
    if len(values) < 2:
        return 0.0
    positives = [value for value in values if value >= 0.90]
    negatives = [value for value in values if value <= -0.90]
    if len(positives) >= 2 and not any(value <= -2.0 for value in values):
        return min(3.0, statistics.mean(positives) + 0.35 * (len(positives) - 1))
    if len(negatives) >= 2 and not any(value >= 2.0 for value in values):
        return max(-3.0, statistics.mean(negatives) - 0.35 * (len(negatives) - 1))
    if positives and negatives:
        return 0.0
    return statistics.mean(values) * 0.35


def _pressure(setup_scores: Mapping[int, float | None]) -> float | None:
    weights = {10: 0.45, 20: 0.35, 60: 0.20}
    values = [
        (score, weights[window])
        for window, score in setup_scores.items()
        if score is not None
    ]
    if len(values) < 2:
        return None
    total_weight = sum(weight for _, weight in values)
    base = sum(score * weight for score, weight in values) / total_weight
    agreement = _agreement_score(setup_scores)
    return max(-3.4, min(3.4, base + 0.22 * agreement))



def pressure_color(score: float | None, *, uncertain: bool = False) -> str:
    if score is None:
        return WHITE
    if uncertain:
        return BROWN
    if score >= 2.55:
        return PURPLE
    if score >= 1.15:
        return GREEN
    if score >= 0.35:
        return BLUE
    if score <= -1.15:
        return RED
    if score <= -0.35:
        return ORANGE
    return YELLOW


def _window_data_quality(
    *,
    current_volume: float | None,
    price_changes: Mapping[int, float | None],
    volume_changes: Mapping[int, float | None],
    minimum_volume: float,
    maximum_volume_jump_pct: float,
) -> tuple[dict[int, str], dict[int, str], str]:
    quality: dict[int, str] = {}
    reasons: dict[int, str] = {}
    for window in WINDOWS:
        price = price_changes.get(window)
        volume = volume_changes.get(window)
        if price is None or volume is None:
            quality[window] = "insufficient"
            reasons[window] = "Vergleichspunkt fehlt"
        elif current_volume is None or current_volume <= 0:
            quality[window] = "uncertain"
            reasons[window] = "aktuelles Volumen fehlt"
        elif current_volume < minimum_volume:
            quality[window] = "uncertain"
            reasons[window] = "sehr geringe Liquidität"
        elif abs(volume) > maximum_volume_jump_pct:
            quality[window] = "uncertain"
            reasons[window] = f"unplausibler Volumensprung {volume:.1f}%"
        else:
            quality[window] = "good"
    good = sum(value == "good" for value in quality.values())
    usable = sum(value != "insufficient" for value in quality.values())
    overall = "good" if good >= 2 else ("uncertain" if usable >= 2 else "insufficient")
    return quality, reasons, overall



def _core_condition_counts(
    *,
    setup_scores: Mapping[int, float | None],
    agreement_score: float,
    relative_color: str,
    p_color: str,
    is_reference: bool,
) -> tuple[int, int]:
    buy = sum(score is not None and score >= 0.90 for score in setup_scores.values())
    sell = sum(score is not None and score <= -0.90 for score in setup_scores.values())
    buy += int(agreement_score >= 0.90)
    sell += int(agreement_score <= -0.90)
    if not is_reference:
        buy += int(relative_color in {GREEN, PURPLE})
        sell += int(relative_color in {ORANGE, RED})
    buy += int(p_color in {GREEN, PURPLE})
    sell += int(p_color in {ORANGE, RED})
    return buy, sell



def _anomaly_score(
    *,
    setup_scores: Mapping[int, float | None],
    agreement_score: float,
    relative_color: str,
    p_score: float | None,
    week_pct: float,
    quality: str,
    fallback_hour_pct: float,
    fallback_day_pct: float,
) -> float:
    weights = {10: 4.8, 20: 3.8, 60: 2.6}
    usable = [score for score in setup_scores.values() if score is not None]
    score = sum(abs(value) * weights[window] for window, value in setup_scores.items() if value is not None)
    score += abs(agreement_score) * 5.0
    score += abs(color_level(relative_color)) * 2.0
    if p_score is not None:
        score += min(abs(p_score), 3.4) * 3.5
    score += min(abs(week_pct) / 3.0, 3.0) * 0.55
    if len(usable) < 2:
        score += min(abs(fallback_hour_pct) * 8.0 + abs(fallback_day_pct) * 1.8, 15.0)
    if quality == "uncertain":
        score *= 0.84
    elif quality == "insufficient":
        score *= 0.58
    return score



def pre_anomaly_score(current: Mapping[str, Any], btc: Mapping[str, Any]) -> float:
    """Cheap pool-wide ranking based only on one fresh /coins/map call."""
    delta = current.get("delta") or {}
    btc_delta = btc.get("delta") or {}
    hour = delta_to_pct(delta.get("hour"))
    day = delta_to_pct(delta.get("day"))
    week = delta_to_pct(delta.get("week"))
    rel_hour = hour - delta_to_pct(btc_delta.get("hour"))
    rel_day = day - delta_to_pct(btc_delta.get("day"))
    rel_week = week - delta_to_pct(btc_delta.get("week"))
    volume = max(float(current.get("volume") or 0.0), 0.0)
    cap = max(float(current.get("cap") or 0.0), 0.0)
    turnover = (volume / cap * 100.0) if cap > 0 else 0.0
    return (
        abs(hour) * 6.0
        + abs(rel_hour) * 3.5
        + abs(day) * 1.25
        + abs(rel_day) * 0.85
        + abs(week) * 0.22
        + abs(rel_week) * 0.18
        + min(turnover, 25.0) * 0.10
    )


def build_short_metrics(
    *,
    current: Mapping[str, Any],
    short_history: list[PricePoint],
    now_ms: int,
    btc_price_changes: Mapping[int, float | None] | None,
    config: Mapping[str, Any],
    is_reference: bool,
) -> ShortMetrics:
    rate = float(current["rate"])
    raw_volume = current.get("volume")
    current_volume = float(raw_volume) if raw_volume not in (None, "") else None
    price_changes, volume_changes = compute_window_changes_from_history(
        current_rate=rate,
        current_volume=current_volume,
        history=short_history,
        now_ms=now_ms,
    )
    window_quality, quality_reasons, quality = _window_data_quality(
        current_volume=current_volume,
        price_changes=price_changes,
        volume_changes=volume_changes,
        minimum_volume=float(config.get("minimum_reliable_volume_usd", 500_000)),
        maximum_volume_jump_pct=float(config.get("maximum_plausible_volume_jump_pct", 500.0)),
    )
    volume_colors = {
        window: signed_color(
            volume_changes[window],
            light=_thresholds(config, "volume", window)[0],
            clear=_thresholds(config, "volume", window)[1],
            strong=_thresholds(config, "volume", window)[2],
            uncertain=window_quality[window] == "uncertain",
        )
        if window_quality[window] != "insufficient"
        else WHITE
        for window in WINDOWS
    }
    setup_scores = {
        window: _window_setup_score(
            price_changes.get(window),
            volume_changes.get(window),
            window=window,
            config=config,
            quality=window_quality[window],
        )
        for window in WINDOWS
    }
    agreement = _agreement_score(setup_scores)
    if is_reference:
        relative_short = 0.0
        relative_color = YELLOW
    else:
        relative_short = _weighted_relative(price_changes, btc_price_changes or {})
        relative_color = signed_color(
            relative_short,
            light=float(config.get("relative_light_pct", 0.12)),
            clear=float(config.get("relative_clear_pct", 0.40)),
            strong=float(config.get("relative_strong_pct", 1.20)),
        )
    p_score = _pressure(setup_scores)
    p_color = pressure_color(p_score, uncertain=quality == "uncertain")
    buy_count, sell_count = _core_condition_counts(
        setup_scores=setup_scores,
        agreement_score=agreement,
        relative_color=relative_color,
        p_color=p_color,
        is_reference=is_reference,
    )
    if buy_count > sell_count:
        direction = "▲"
    elif sell_count > buy_count:
        direction = "▼"
    elif p_score is not None and p_score > 0.20:
        direction = "▲"
    elif p_score is not None and p_score < -0.20:
        direction = "▼"
    else:
        direction = "="
    if quality == "insufficient":
        signal = WHITE
    elif quality == "uncertain":
        signal = BROWN
    else:
        signal = p_color
    delta = current.get("delta") or {}
    week_pct = delta_to_pct(delta.get("week"))
    hour_pct = delta_to_pct(delta.get("hour"))
    day_pct = delta_to_pct(delta.get("day"))
    anomaly = _anomaly_score(
        setup_scores=setup_scores,
        agreement_score=agreement,
        relative_color=relative_color,
        p_score=p_score,
        week_pct=week_pct,
        quality=quality,
        fallback_hour_pct=hour_pct,
        fallback_day_pct=day_pct,
    )
    return ShortMetrics(
        price_changes=price_changes,
        volume_changes=volume_changes,
        volume_colors=volume_colors,
        relative_short_pct=relative_short,
        relative_color=relative_color,
        pressure_score=p_score,
        pressure_color=p_color,
        buy_count=buy_count,
        sell_count=sell_count,
        direction=direction,
        signal_color=signal,
        anomaly_score=anomaly,
        data_quality=quality,
        window_quality=window_quality,
        quality_reasons=quality_reasons,
        window_setup_scores=setup_scores,
        agreement_score=agreement,
    )



def _median(values: Iterable[float]) -> float | None:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.median(cleaned) if cleaned else None


def _return_observations(
    points: list[PricePoint], timezone: str, block_hours: int
) -> tuple[list[tuple[int, int | None, float, float | None]], float | None]:
    """Create interval observations with price return and rolling-volume change.

    Returns are normalized for elapsed time so mixed LCW history resolutions remain
    comparable. Volume is confirmation: rising volume strengthens the direction of
    the price move, while falling volume weakens it.
    """
    if len(points) < 2:
        return [], None
    intervals = [
        (current.timestamp_ms - previous.timestamp_ms) / 3_600_000
        for previous, current in zip(points, points[1:])
        if current.timestamp_ms > previous.timestamp_ms
    ]
    median_interval = _median(intervals)
    if median_interval is None:
        return [], None
    lower = max(5 / 60, median_interval * 0.20)
    upper = min(72.0, median_interval * 5.0)
    tz = ZoneInfo(timezone)
    observations: list[tuple[int, int | None, float, float | None]] = []
    for previous, current in zip(points, points[1:]):
        elapsed_hours = (current.timestamp_ms - previous.timestamp_ms) / 3_600_000
        if elapsed_hours < lower or elapsed_hours > upper or previous.rate <= 0:
            continue
        raw_return = (current.rate / previous.rate - 1.0) * 100.0
        price_adjusted = raw_return / max(math.sqrt(elapsed_hours), 1.0)
        volume_adjusted: float | None = None
        if (
            previous.volume is not None
            and current.volume is not None
            and previous.volume > 0
        ):
            raw_volume = (current.volume / previous.volume - 1.0) * 100.0
            if math.isfinite(raw_volume) and abs(raw_volume) <= 500.0:
                volume_adjusted = raw_volume / max(math.sqrt(elapsed_hours), 1.0)
        local_dt = datetime.fromtimestamp(current.timestamp_ms / 1000, tz=tz)
        block = (local_dt.hour // block_hours) * block_hours if median_interval <= 8.0 else None
        observations.append((local_dt.weekday(), block, price_adjusted, volume_adjusted))
    return observations, median_interval


def _trimmed_mean(values: list[float], trim_ratio: float = 0.10) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    trim = int(len(ordered) * trim_ratio)
    if trim > 0 and len(ordered) - 2 * trim >= 3:
        ordered = ordered[trim:-trim]
    return statistics.mean(ordered)


def _combined_time_scores(
    raw: list[tuple[int, int | None, float, float | None]]
) -> list[tuple[int, int | None, float]]:
    if not raw:
        return []
    price_scale = _median(abs(item[2]) for item in raw) or 0.01
    volume_values = [abs(item[3]) for item in raw if item[3] is not None]
    volume_scale = _median(volume_values) or 0.10
    combined: list[tuple[int, int | None, float]] = []
    for weekday, block, price_value, volume_value in raw:
        price_norm = max(-3.0, min(3.0, price_value / max(price_scale, 1e-9)))
        if volume_value is None:
            score = price_norm * 0.55
        else:
            volume_norm = max(-3.0, min(3.0, volume_value / max(volume_scale, 1e-9)))
            score = _relationship_score(price_norm, volume_norm)
        combined.append((weekday, block, score))
    baseline = 0.55 * statistics.median(item[2] for item in combined) + 0.45 * _trimmed_mean(
        [item[2] for item in combined]
    )
    return [(weekday, block, score - baseline) for weekday, block, score in combined]



def _time_summary(values: list[float], min_samples: int) -> tuple[str, float, float]:
    """Return robust classification, central score and confidence.

    A directional color needs enough samples, a consistent hit rate and a robust
    central value. Low-confidence data is brown instead of falsely neutral/positive.
    """
    if len(values) < min_samples:
        return "?", 0.0, min(1.0, len(values) / max(min_samples, 1))
    median_value = statistics.median(values)
    mean_value = _trimmed_mean(values)
    central = 0.60 * median_value + 0.40 * mean_value
    hit_rate = sum(value > 0 for value in values) / len(values)
    sample_confidence = min(1.0, len(values) / 12.0)
    consistency = abs(hit_rate - 0.5) * 2.0
    confidence = sample_confidence * (0.48 + 0.52 * consistency)

    if confidence < 0.38:
        return "?", central, confidence
    if central >= 0.85 and hit_rate >= 0.64 and confidence >= 0.56:
        return "++", central, confidence
    if central >= 0.28 and hit_rate >= 0.57 and confidence >= 0.42:
        return "+", central, confidence
    if central <= -0.85 and hit_rate <= 0.36 and confidence >= 0.56:
        return "--", central, confidence
    if central <= -0.28 and hit_rate <= 0.43 and confidence >= 0.42:
        return "-", central, confidence
    return "=", central, confidence


def _weekday_quality(values: list[float]) -> float:
    central = 0.60 * statistics.median(values) + 0.40 * _trimmed_mean(values)
    hit_rate = sum(value > 0 for value in values) / len(values)
    sample_factor = min(1.0, len(values) / 8.0)
    return central * (0.55 + 0.45 * hit_rate) * (0.70 + 0.30 * sample_factor)


def analyze_seasonality(
    points: list[PricePoint],
    now: datetime,
    timezone: str,
    block_hours: int = 4,
    min_samples: int = 4,
    minimum_observations: int = 20,
) -> Seasonality:
    raw_observations, _ = _return_observations(points, timezone, block_hours)
    observations = _combined_time_scores(raw_observations)
    if len(observations) < 7:
        return Seasonality("?", tuple(), len(observations), "insufficient")

    by_slot: dict[tuple[int, int], list[float]] = {}
    by_weekday: dict[int, list[float]] = {}
    for weekday, block, value in observations:
        if block is not None:
            by_slot.setdefault((weekday, block), []).append(value)
        by_weekday.setdefault(weekday, []).append(value)

    weekday_min = max(3, min_samples)
    eligible = {
        weekday: values
        for weekday, values in by_weekday.items()
        if len(values) >= weekday_min
    }
    # Prefer well-sampled weekdays. If LCW returned a coarse history, fall back to
    # weekdays with at least two observations so the two strongest days remain usable.
    rankable = eligible
    if len(rankable) < 2:
        rankable = {
            weekday: values
            for weekday, values in by_weekday.items()
            if len(values) >= 1
        }
    ranked_by_score = sorted(
        rankable, key=lambda weekday: _weekday_quality(rankable[weekday]), reverse=True
    )
    selected_weekdays = ranked_by_score[:2]
    # Display chronologically, but with Saturday as the beginning of the week.
    selected_weekdays.sort(key=DISPLAY_WEEK_ORDER.index)
    best_days = tuple(DAY_NAMES[weekday] for weekday in selected_weekdays)
    weekday_scores = {
        DAY_NAMES[weekday]: round(_weekday_quality(values), 4)
        for weekday, values in rankable.items()
    }

    local_now = now.astimezone(ZoneInfo(timezone))
    current_block = (local_now.hour // block_hours) * block_hours
    slot_min = max(6, min_samples)
    current_slot = by_slot.get((local_now.weekday(), current_block), [])
    if len(current_slot) >= slot_min and len(observations) >= minimum_observations:
        current, score, confidence = _time_summary(current_slot, slot_min)
        source = "weekday-block"
    else:
        current_day = by_weekday.get(local_now.weekday(), [])
        day_min = max(5, min_samples)
        if len(current_day) >= day_min and len(observations) >= minimum_observations:
            current, score, confidence = _time_summary(current_day, day_min)
            source = "weekday"
        else:
            current, score, confidence = "?", 0.0, 0.0
            source = "insufficient"
    return Seasonality(
        current,
        best_days,
        len(observations),
        source,
        current_score=score,
        current_confidence=confidence,
        weekday_scores=weekday_scores,
    )


def _normalized_signal_value(
    value: float, *, light: float, clear: float, strong: float
) -> float:
    """Map a signed percentage change to a compact -3..+3 strength scale."""
    absolute = abs(value)
    if absolute >= strong:
        level = 3.0
    elif absolute >= clear:
        level = 2.0
    elif absolute >= light:
        level = 1.0
    else:
        level = min(0.45, absolute / max(light, 1e-9) * 0.45)
    return level if value >= 0 else -level


def current_now_signal(
    short: ShortMetrics,
    seasonality: Seasonality,
    config: Mapping[str, Any],
    *,
    is_reference: bool,
) -> tuple[float | None, str]:
    """Fresh demand/distribution signal from the same setup logic as X/8."""
    if short.data_quality == "insufficient":
        return None, WHITE
    values = [
        (short.window_setup_scores.get(window), weight)
        for window, weight in {10: 0.45, 20: 0.35, 60: 0.20}.items()
        if short.window_setup_scores.get(window) is not None
    ]
    if len(values) < 2:
        return None, BROWN if short.data_quality == "uncertain" else WHITE
    total_weight = sum(weight for _, weight in values)
    score = sum(value * weight for value, weight in values) / total_weight
    score += 0.28 * short.agreement_score
    if not is_reference:
        score += 0.12 * color_level(short.relative_color)
    if seasonality.current_score is not None and seasonality.current_confidence >= 0.42:
        historical = max(-1.0, min(1.0, seasonality.current_score / 0.85))
        score += historical * min(seasonality.current_confidence, 0.85) * 0.25
    raw = config.get("now_signal", {})
    light = float(raw.get("light", 0.35)) if isinstance(raw, Mapping) else 0.35
    clear = float(raw.get("clear", 1.05)) if isinstance(raw, Mapping) else 1.05
    strong = float(raw.get("strong", 2.20)) if isinstance(raw, Mapping) else 2.20
    color = signed_color(score, light=light, clear=clear, strong=strong)
    if short.data_quality == "uncertain" and color == YELLOW:
        color = BROWN
    return score, color



def time_color(mark: str) -> str:
    return {
        "++": PURPLE,
        "+": GREEN,
        "=": YELLOW,
        "-": ORANGE,
        "--": RED,
        "?": BROWN,
    }.get(mark, BROWN)

def week_color(week_pct: float) -> str:
    return signed_color(week_pct, light=0.75, clear=3.0, strong=10.0)


def btc_gate(short: ShortMetrics, config: Mapping[str, Any]) -> bool:
    """Green BTC gate: at least 2/3 positive setups and no strong negative one."""
    values = [score for score in short.window_setup_scores.values() if score is not None]
    positives = sum(score >= 0.90 for score in values)
    strong_negative = any(score <= -2.0 for score in values)
    return positives >= 2 and not strong_negative


def _finalize_condition_counts(
    short: ShortMetrics,
    *,
    week_signal: str,
    now_signal: str,
    is_reference: bool,
) -> None:
    buy, sell = _core_condition_counts(
        setup_scores=short.window_setup_scores,
        agreement_score=short.agreement_score,
        relative_color=short.relative_color,
        p_color=short.pressure_color,
        is_reference=is_reference,
    )
    buy += int(now_signal in {GREEN, PURPLE})
    sell += int(now_signal in {ORANGE, RED})
    buy += int(week_signal in {GREEN, PURPLE})
    sell += int(week_signal in {ORANGE, RED})
    short.buy_count = buy
    short.sell_count = sell
    if buy > sell:
        short.direction = "▲"
    elif sell > buy:
        short.direction = "▼"
    else:
        short.direction = "="
    if short.data_quality == "insufficient":
        short.signal_color = WHITE
    elif short.data_quality == "uncertain":
        short.signal_color = BROWN
    elif buy > sell:
        positive = max(
            [score for score in short.window_setup_scores.values() if score is not None] +
            [short.pressure_score or 0.0]
        )
        short.signal_color = PURPLE if positive >= 2.5 else (GREEN if positive >= 1.0 else BLUE)
    elif sell > buy:
        negative = min(
            [score for score in short.window_setup_scores.values() if score is not None] +
            [short.pressure_score or 0.0]
        )
        short.signal_color = RED if negative <= -1.4 else ORANGE
    else:
        short.signal_color = YELLOW



def build_coin_analysis(
    *,
    display_code: str,
    api_code: str,
    current: Mapping[str, Any],
    short: ShortMetrics,
    history: list[PricePoint],
    now: datetime,
    timezone: str,
    block_hours: int,
    min_samples: int,
    minimum_observations: int,
    is_reference: bool,
    config: Mapping[str, Any],
) -> CoinAnalysis:
    week_pct = delta_to_pct((current.get("delta") or {}).get("week"))
    seasonality = analyze_seasonality(
        history,
        now,
        timezone,
        block_hours=block_hours,
        min_samples=min_samples,
        minimum_observations=minimum_observations,
    )
    now_score, now_color = current_now_signal(
        short, seasonality, config, is_reference=is_reference
    )
    week_signal = week_color(week_pct)
    _finalize_condition_counts(
        short,
        week_signal=week_signal,
        now_signal=now_color,
        is_reference=is_reference,
    )
    return CoinAnalysis(
        display_code=display_code,
        api_code=api_code,
        price=float(current["rate"]),
        week_pct=week_pct,
        week_color=week_signal,
        short=short,
        seasonality=seasonality,
        now_score=now_score,
        now_color=now_color,
        is_reference=is_reference,
        btc_gate=btc_gate(short, config) if is_reference else False,
    )


def strength_count(item: CoinAnalysis) -> int:
    return max(item.short.buy_count, item.short.sell_count)


def confidence_sort_key(item: CoinAnalysis) -> tuple[float, ...]:
    """Strict visible count first; strongest early relationship signal breaks ties."""
    quality_rank = {"good": 2, "uncertain": 1, "insufficient": 0}.get(
        item.short.data_quality, 0
    )
    directional_margin = abs(item.short.buy_count - item.short.sell_count)
    setup_intensity = sum(
        abs(score) for score in item.short.window_setup_scores.values() if score is not None
    )
    return (
        float(strength_count(item)),
        float(directional_margin),
        float(abs(item.short.agreement_score)),
        float(setup_intensity),
        float(quality_rank),
        float(item.short.anomaly_score),
    )



def format_line(item: CoinAnalysis, *, generated_at: datetime, timezone: str) -> str:
    volumes = "".join(item.short.volume_colors.get(window, WHITE) for window in WINDOWS)
    count = strength_count(item)
    weekday_suffix = "".join(item.seasonality.best_weekdays[:2])
    if item.is_reference:
        minute_text = generated_at.astimezone(ZoneInfo(timezone)).strftime(":%M")
        market_gate = GREEN if item.btc_gate else BLACK
        # One BTC circle, then one literal space replacing the former second circle.
        return (
            f"{market_gate} {minute_text} {count}{item.short.direction}"
            f"7{item.week_color}B{market_gate}P{item.short.pressure_color}"
            f"V{volumes}N{item.now_color}{weekday_suffix}"
        )
    code = display_code(item.display_code)
    return (
        f"{item.short.signal_color}{code}{count}{item.short.direction}"
        f"7{item.week_color}B{item.short.relative_color}"
        f"P{item.short.pressure_color}V{volumes}"
        f"N{item.now_color}{weekday_suffix}"
    )



def build_report(
    reference: CoinAnalysis,
    top_coins: list[CoinAnalysis],
    *,
    generated_at: datetime,
    timezone: str,
) -> str:
    # Reference line is fixed. All coin lines follow strict X/8 confidence order.
    ordered = sorted(top_coins, key=confidence_sort_key, reverse=True)
    lines = [format_line(reference, generated_at=generated_at, timezone=timezone)]
    lines.extend(format_line(item, generated_at=generated_at, timezone=timezone) for item in ordered)
    return "\n".join(lines)

def analysis_to_dict(item: CoinAnalysis) -> dict[str, Any]:
    return asdict(item)

