"""Deterministic high-confidence crypto extreme analysis for Discord (v3.2.4)."""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

DAY_NAMES = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
DISPLAY_WEEK_ORDER = (5, 6, 0, 1, 2, 3, 4)
WINDOWS = (10, 20, 60)
WINDOW_WEIGHTS = {10: 0.45, 20: 0.35, 60: 0.20}

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


@dataclass(frozen=True)
class RobustBaseline:
    median: float
    scale: float
    samples: int


@dataclass
class Seasonality:
    current: str
    best_weekdays: tuple[str, ...]
    samples: int
    source: str
    current_score: float | None = None
    current_confidence: float = 0.0
    weekday_scores: dict[str, float] = field(default_factory=dict)
    weekday_confidence: dict[str, float] = field(default_factory=dict)


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
    accumulation_windows: dict[int, float | None] = field(default_factory=dict)
    distribution_windows: dict[int, float | None] = field(default_factory=dict)
    accumulation_score: float = 0.0
    distribution_score: float = 0.0
    extreme_proximity: float = 0.0
    pattern_confidence: float = 0.0
    acceleration_score: float = 0.0
    relative_window_scores: dict[int, float | None] = field(default_factory=dict)
    price_baselines: dict[int, RobustBaseline] = field(default_factory=dict)
    volume_baselines: dict[int, RobustBaseline] = field(default_factory=dict)
    price_strengths: dict[int, float | None] = field(default_factory=dict)
    volume_strengths: dict[int, float | None] = field(default_factory=dict)


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
    week_percentile: float | None = None
    week_confidence: float = 0.0


@dataclass(frozen=True)
class TimeObservation:
    timestamp_ms: int
    weekday: int
    block: int | None
    score: float


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


def abbreviate_code(code: str) -> str:
    cleaned = "".join(char for char in code.upper() if char.isalnum())
    if cleaned in CODE_ALIASES:
        return CODE_ALIASES[cleaned]
    return cleaned if len(cleaned) <= 3 else cleaned[:3]


def display_code(code: str) -> str:
    """Names are printed at line end, therefore no padding is needed."""
    return abbreviate_code(code)


def _thresholds(config: Mapping[str, Any], name: str, window: int) -> tuple[float, float, float]:
    raw = config.get(name, {}) if isinstance(config, Mapping) else {}
    item = raw.get(str(window), {}) if isinstance(raw, Mapping) else {}
    light = float(item.get("light", 0.10))
    clear = float(item.get("clear", 0.35))
    strong = float(item.get("strong", 1.20))
    if not (0 <= light <= clear <= strong):
        raise ValueError(f"Ungültige Schwellen für {name}/{window}.")
    return light, clear, strong


def color_level(color: str) -> int:
    return {
        PURPLE: 3,
        GREEN: 2,
        BLUE: 1,
        YELLOW: 0,
        ORANGE: -1,
        RED: -3,
        BROWN: 0,
        WHITE: 0,
        BLACK: 0,
    }.get(color, 0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _median(values: Iterable[float]) -> float | None:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.median(cleaned) if cleaned else None


def _trimmed_mean(values: Sequence[float], trim_ratio: float = 0.10) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return 0.0
    trim = int(len(ordered) * trim_ratio)
    if trim > 0 and len(ordered) - 2 * trim >= 3:
        ordered = ordered[trim:-trim]
    return statistics.mean(ordered)


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return 0.0
    index = _clamp(fraction, 0.0, 1.0) * (len(ordered) - 1)
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _weighted_median(values: Sequence[tuple[float, float]]) -> float:
    cleaned = sorted((float(v), max(0.0, float(w))) for v, w in values if math.isfinite(float(v)))
    if not cleaned:
        return 0.0
    total = sum(weight for _, weight in cleaned)
    if total <= 0:
        return statistics.median(value for value, _ in cleaned)
    target = total / 2.0
    running = 0.0
    for value, weight in cleaned:
        running += weight
        if running >= target:
            return value
    return cleaned[-1][0]


def _robust_baseline(values: Sequence[float], fallback_scale: float) -> RobustBaseline:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    if not cleaned:
        return RobustBaseline(0.0, max(fallback_scale, 1e-6), 0)
    median_value = statistics.median(cleaned)
    deviations = [abs(value - median_value) for value in cleaned]
    mad = statistics.median(deviations) if deviations else 0.0
    mad_scale = 1.4826 * mad
    iqr_scale = (_percentile(cleaned, 0.75) - _percentile(cleaned, 0.25)) / 1.349
    std_scale = statistics.pstdev(cleaned) if len(cleaned) >= 2 else 0.0
    candidates = [scale for scale in (mad_scale, iqr_scale, std_scale * 0.60) if scale > 0]
    scale = max(min(candidates) if candidates else 0.0, fallback_scale, 1e-6)
    return RobustBaseline(median_value, scale, len(cleaned))


def _robust_z(value: float | None, baseline: RobustBaseline) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return _clamp((value - baseline.median) / max(baseline.scale, 1e-9), -6.0, 6.0)


def _nearest_point(points: Sequence[PricePoint], target_ms: int, max_distance_ms: int) -> PricePoint | None:
    if not points:
        return None
    timestamps = [point.timestamp_ms for point in points]
    index = bisect.bisect_left(timestamps, target_ms)
    candidates = []
    if index < len(points):
        candidates.append(points[index])
    if index > 0:
        candidates.append(points[index - 1])
    if not candidates:
        return None
    best = min(candidates, key=lambda point: abs(point.timestamp_ms - target_ms))
    return best if abs(best.timestamp_ms - target_ms) <= max_distance_ms else None


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
        tolerance_minutes = max(4.0, min(12.0, window * 0.30))
        point = _nearest_point(usable, target_ms, int(tolerance_minutes * 60_000))
        price_changes[window] = _pct(current_rate, point.rate if point else None)
        volume_changes[window] = _pct(current_volume, point.volume if point else None)
    return price_changes, volume_changes


def _rolling_window_samples(history: list[PricePoint], window: int) -> tuple[list[float], list[float]]:
    if len(history) < 3:
        return [], []
    price_samples: list[float] = []
    volume_samples: list[float] = []
    tolerance_ms = int(max(4.0, min(12.0, window * 0.30)) * 60_000)
    min_spacing_ms = max(4, window // 3) * 60_000
    last_endpoint = -10**30
    for current in history:
        if current.timestamp_ms - last_endpoint < min_spacing_ms:
            continue
        previous = _nearest_point(history, current.timestamp_ms - window * 60_000, tolerance_ms)
        if previous is None or previous.timestamp_ms >= current.timestamp_ms:
            continue
        price_value = _pct(current.rate, previous.rate)
        volume_value = _pct(current.volume, previous.volume)
        if price_value is not None and math.isfinite(price_value):
            price_samples.append(price_value)
        if volume_value is not None and math.isfinite(volume_value) and abs(volume_value) <= 2000:
            volume_samples.append(volume_value)
        last_endpoint = current.timestamp_ms
    return price_samples, volume_samples


def _window_baselines(history: list[PricePoint], config: Mapping[str, Any]) -> tuple[dict[int, RobustBaseline], dict[int, RobustBaseline]]:
    price: dict[int, RobustBaseline] = {}
    volume: dict[int, RobustBaseline] = {}
    for window in WINDOWS:
        p_samples, v_samples = _rolling_window_samples(history, window)
        p_light, _, _ = _thresholds(config, "price", window)
        v_light, _, _ = _thresholds(config, "volume", window)
        price[window] = _robust_baseline(p_samples, p_light * 0.75)
        volume[window] = _robust_baseline(v_samples, v_light * 0.75)
    return price, volume


def _absolute_level(value: float, *, light: float, clear: float, strong: float) -> float:
    absolute = abs(value)
    if absolute >= strong:
        level = 3.0
    elif absolute >= clear:
        level = 1.6 + 1.4 * (absolute - clear) / max(strong - clear, 1e-9)
    elif absolute >= light:
        level = 0.55 + 1.05 * (absolute - light) / max(clear - light, 1e-9)
    else:
        level = 0.55 * absolute / max(light, 1e-9)
    return level if value >= 0 else -level


def _robust_signed_strength(
    value: float | None,
    baseline: RobustBaseline,
    thresholds: tuple[float, float, float],
) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    absolute_level = _absolute_level(value, light=thresholds[0], clear=thresholds[1], strong=thresholds[2])
    z = _robust_z(value, baseline) or 0.0
    direction = 1.0 if value >= 0 else -1.0
    unusual_same_direction = max(0.0, direction * z)
    magnitude = 0.62 * abs(absolute_level) + 0.38 * min(unusual_same_direction, 4.0)
    return direction * _clamp(magnitude, 0.0, 4.0)


def _strict_gradient_color(
    score: float | None,
    *,
    extreme_positive: bool = False,
    extreme_negative: bool = False,
    uncertain: bool = False,
    insufficient: bool = False,
) -> str:
    if insufficient or score is None or not math.isfinite(score):
        return WHITE
    if uncertain:
        return BROWN
    if extreme_positive:
        return PURPLE
    if extreme_negative:
        return RED
    if score >= 1.15:
        return GREEN
    if score >= 0.35:
        return BLUE
    if score <= -1.15:
        return ORANGE
    if score <= -0.35:
        return ORANGE
    return YELLOW


def _volume_color(
    value: float | None,
    strength: float | None,
    baseline: RobustBaseline,
    quality: str,
) -> str:
    if quality == "insufficient" or value is None or strength is None:
        return WHITE
    if quality == "uncertain":
        return BROWN
    z = _robust_z(value, baseline) or 0.0
    extreme_positive = baseline.samples >= 8 and value > 0 and strength >= 2.65 and z >= 2.6
    extreme_negative = baseline.samples >= 8 and value < 0 and strength <= -2.65 and z <= -2.6
    return _strict_gradient_color(
        strength,
        extreme_positive=extreme_positive,
        extreme_negative=extreme_negative,
    )


def _window_data_quality(
    *,
    current_volume: float | None,
    price_changes: Mapping[int, float | None],
    volume_changes: Mapping[int, float | None],
    volume_baselines: Mapping[int, RobustBaseline],
    minimum_volume: float,
    maximum_volume_jump_pct: float,
) -> tuple[dict[int, str], dict[int, str], str]:
    quality: dict[int, str] = {}
    reasons: dict[int, str] = {}
    for window in WINDOWS:
        price = price_changes.get(window)
        volume = volume_changes.get(window)
        baseline = volume_baselines.get(window, RobustBaseline(0.0, 1.0, 0))
        if price is None or volume is None:
            quality[window] = "insufficient"
            reasons[window] = "Vergleichspunkt fehlt"
        elif current_volume is None or current_volume <= 0:
            quality[window] = "uncertain"
            reasons[window] = "aktuelles Volumen fehlt"
        elif current_volume < minimum_volume:
            quality[window] = "uncertain"
            reasons[window] = "geringe Liquidität"
        else:
            z = abs(_robust_z(volume, baseline) or 0.0)
            impossible_jump = abs(volume) > maximum_volume_jump_pct and (baseline.samples < 6 or z > 5.8)
            if impossible_jump:
                quality[window] = "uncertain"
                reasons[window] = f"auffälliger Volumensprung {volume:.1f}%"
            else:
                quality[window] = "good"
    good = sum(value == "good" for value in quality.values())
    usable = sum(value != "insufficient" for value in quality.values())
    overall = "good" if good >= 2 else ("uncertain" if usable >= 2 else "insufficient")
    return quality, reasons, overall


def _window_pattern(
    *,
    price_change: float | None,
    volume_change: float | None,
    price_strength: float | None,
    volume_strength: float | None,
    window: int,
    config: Mapping[str, Any],
    quality: str,
) -> tuple[float | None, float | None, float | None]:
    """Return accumulation, distribution and pressure for one window (0..100, 0..100, -3..3)."""
    if quality != "good" or None in (price_change, volume_change, price_strength, volume_strength):
        return None, None, None
    assert price_change is not None and volume_change is not None
    assert price_strength is not None and volume_strength is not None
    p_light, p_clear, _ = _thresholds(config, "price", window)

    stable_limit = max(p_clear * 0.85, p_light * 1.40)
    price_stability = _clamp(1.0 - abs(price_change) / max(stable_limit * 1.55, 1e-9), 0.0, 1.0)
    slight_up = _clamp((price_strength + 0.25) / 1.50, 0.0, 1.0)
    no_drop = 1.0 if price_change >= 0 else _clamp(1.0 + price_change / max(p_light * 1.25, 1e-9), 0.0, 1.0)
    volume_lead = volume_strength - max(price_strength, 0.0) * 0.55
    volume_force = _clamp((volume_strength - 0.65) / 2.35, 0.0, 1.0)
    lead_force = _clamp((volume_lead - 0.55) / 2.25, 0.0, 1.0)
    accumulation = 100.0 * volume_force * (0.82 * price_stability + 0.18 * slight_up) * no_drop
    accumulation *= 0.72 + 0.28 * lead_force

    price_rise = _clamp((price_strength - 0.45) / 2.55, 0.0, 1.0)
    lag = price_strength - volume_strength
    lag_force = _clamp((lag - 0.65) / 2.35, 0.0, 1.0)
    nonconfirm = _clamp((0.35 - volume_strength) / 2.35, 0.0, 1.0)
    distribution = 100.0 * price_rise * (0.72 * lag_force + 0.28 * nonconfirm)

    confirmed_selling = _clamp((-price_strength - 0.35) / 2.40, 0.0, 1.0) * _clamp(
        (volume_strength - 0.35) / 2.40, 0.0, 1.0
    )
    fading_demand = _clamp((-price_strength - 0.30) / 2.70, 0.0, 1.0) * _clamp(
        (-volume_strength - 0.30) / 2.70, 0.0, 1.0
    )
    positive_pressure = accumulation / 100.0
    negative_pressure = max(distribution / 100.0, confirmed_selling, 0.55 * fading_demand)
    pressure = _clamp((positive_pressure - negative_pressure) * 3.25, -3.25, 3.25)
    return accumulation, distribution, pressure


def _weighted(values: Mapping[int, float | None]) -> float:
    usable = [(values.get(window), WINDOW_WEIGHTS[window]) for window in WINDOWS if values.get(window) is not None]
    if not usable:
        return 0.0
    total = sum(weight for _, weight in usable)
    return sum(float(value) * weight for value, weight in usable) / total


def _pattern_aggregate(
    accumulation: Mapping[int, float | None],
    distribution: Mapping[int, float | None],
) -> tuple[float, float, float, float]:
    acc = _weighted(accumulation)
    dist = _weighted(distribution)
    acc_values = [value for value in accumulation.values() if value is not None]
    dist_values = [value for value in distribution.values() if value is not None]
    if sum(value >= 55 for value in acc_values) >= 2:
        acc += 7.0
    if sum(value >= 55 for value in dist_values) >= 2:
        dist += 7.0
    acc_acceleration = (accumulation.get(10) or 0.0) - statistics.mean(
        [value for window in (20, 60) if (value := accumulation.get(window)) is not None] or [0.0]
    )
    dist_acceleration = (distribution.get(10) or 0.0) - statistics.mean(
        [value for window in (20, 60) if (value := distribution.get(window)) is not None] or [0.0]
    )
    acc += _clamp(acc_acceleration, 0.0, 20.0) * 0.18
    dist += _clamp(dist_acceleration, 0.0, 20.0) * 0.18
    return _clamp(acc, 0.0, 100.0), _clamp(dist, 0.0, 100.0), acc_acceleration, dist_acceleration


def _strict_accumulation_extreme(
    accumulation: Mapping[int, float | None],
    distribution: Mapping[int, float | None],
    price_changes: Mapping[int, float | None],
    volume_strengths: Mapping[int, float | None],
    config: Mapping[str, Any],
    quality: Mapping[int, str],
    aggregate: float,
) -> bool:
    if any(quality.get(window) != "good" for window in WINDOWS):
        return False
    if aggregate < 82.0:
        return False
    if sum((accumulation.get(window) or 0.0) >= 78.0 for window in WINDOWS) < 2:
        return False
    if any((distribution.get(window) or 0.0) >= 38.0 for window in WINDOWS):
        return False
    for window in WINDOWS:
        p_light, p_clear, _ = _thresholds(config, "price", window)
        price = price_changes.get(window)
        volume_strength = volume_strengths.get(window)
        if price is None or volume_strength is None:
            return False
        if price < -p_light or price > p_clear * 1.20 or volume_strength < 1.55:
            return False
    return True


def _strict_distribution_extreme(
    accumulation: Mapping[int, float | None],
    distribution: Mapping[int, float | None],
    price_strengths: Mapping[int, float | None],
    volume_strengths: Mapping[int, float | None],
    quality: Mapping[int, str],
    aggregate: float,
) -> bool:
    if any(quality.get(window) != "good" for window in WINDOWS):
        return False
    if aggregate < 82.0:
        return False
    if sum((distribution.get(window) or 0.0) >= 76.0 for window in WINDOWS) < 2:
        return False
    if any((accumulation.get(window) or 0.0) >= 38.0 for window in WINDOWS):
        return False
    for window in WINDOWS:
        price_strength = price_strengths.get(window)
        volume_strength = volume_strengths.get(window)
        if price_strength is None or volume_strength is None:
            return False
        if price_strength < 0.70 or price_strength - volume_strength < 0.90:
            return False
    return True


def _pattern_color(
    *,
    accumulation_score: float,
    distribution_score: float,
    strict_accumulation: bool,
    strict_distribution: bool,
    quality: str,
    good_windows: int,
) -> str:
    if good_windows < 2:
        return BROWN if quality == "uncertain" else WHITE
    if strict_accumulation:
        return PURPLE
    if strict_distribution:
        return RED
    margin = accumulation_score - distribution_score
    if margin >= 25.0 and accumulation_score >= 58.0:
        return GREEN
    if margin >= 10.0 and accumulation_score >= 34.0:
        return BLUE
    if margin <= -25.0 and distribution_score >= 58.0:
        return ORANGE
    if margin <= -10.0 and distribution_score >= 34.0:
        return ORANGE
    return YELLOW


def _relative_color(scores: Mapping[int, float | None], *, reference: bool = False) -> str:
    values = [value for value in scores.values() if value is not None]
    if len(values) < 2:
        return WHITE
    if reference:
        weighted = _weighted(scores)
        if len(values) == 3 and min(values) >= 1.7 and sum(value >= 2.2 for value in values) >= 2:
            return PURPLE
        if len(values) == 3 and max(values) <= -1.7 and sum(value <= -2.2 for value in values) >= 2:
            return RED
    else:
        if len(values) == 3 and min(values) >= 1.35 and sum(value >= 2.0 for value in values) >= 2:
            return PURPLE
        if len(values) == 3 and max(values) <= -1.35 and sum(value <= -2.0 for value in values) >= 2:
            return RED
        weighted = _weighted(scores)
    if weighted >= 0.95 and sum(value > 0 for value in values) >= 2:
        return GREEN
    if weighted >= 0.30:
        return BLUE
    if weighted <= -0.95 and sum(value < 0 for value in values) >= 2:
        return ORANGE
    if weighted <= -0.30:
        return ORANGE
    return YELLOW


def _pressure_color(
    score: float | None,
    window_pressures: Mapping[int, float | None],
    *,
    strict_accumulation: bool,
    strict_distribution: bool,
    quality: str,
) -> str:
    values = [value for value in window_pressures.values() if value is not None]
    if len(values) < 2 or score is None:
        return BROWN if quality == "uncertain" else WHITE
    purple = strict_accumulation and min(values) >= 1.25 and sum(value >= 2.1 for value in values) >= 2
    red = (
        (strict_distribution or sum(value <= -2.0 for value in values) >= 2)
        and max(values) <= -0.45
        and score <= -2.0
    )
    return _strict_gradient_color(score, extreme_positive=purple, extreme_negative=red)


def _quality_factor(quality: str, good_windows: int) -> float:
    if quality == "insufficient":
        return 0.45
    if quality == "uncertain":
        return 0.72
    return 0.82 + 0.06 * min(good_windows, 3)


def _condition_counts(
    *,
    accumulation: Mapping[int, float | None],
    distribution: Mapping[int, float | None],
    acc_acceleration: float,
    dist_acceleration: float,
    relative_color: str,
    volume_strengths: Mapping[int, float | None],
    price_strengths: Mapping[int, float | None],
    quality: Mapping[int, str],
) -> tuple[int, int]:
    acc_values = {window: accumulation.get(window) for window in WINDOWS}
    dist_values = {window: distribution.get(window) for window in WINDOWS}
    buy = sum((acc_values[window] or 0.0) >= (50.0 if window == 60 else 55.0) for window in WINDOWS)
    sell = sum((dist_values[window] or 0.0) >= (50.0 if window == 60 else 55.0) for window in WINDOWS)
    buy += int(sum((value or 0.0) >= 55.0 for value in acc_values.values()) >= 2 and not any((value or 0.0) >= 55.0 for value in dist_values.values()))
    sell += int(sum((value or 0.0) >= 55.0 for value in dist_values.values()) >= 2 and not any((value or 0.0) >= 55.0 for value in acc_values.values()))
    buy += int(acc_acceleration >= 8.0 or (acc_values.get(10) or 0.0) >= 78.0)
    sell += int(dist_acceleration >= 8.0 or (dist_values.get(10) or 0.0) >= 78.0)
    buy += int(relative_color in {BLUE, GREEN, PURPLE})
    sell += int(relative_color in {ORANGE, RED})
    buy += int(sum((volume_strengths.get(window) or -9.0) >= 1.25 for window in WINDOWS) >= 2 and any((volume_strengths.get(window) or -9.0) >= 2.0 for window in WINDOWS))
    sell += int(sum(((price_strengths.get(window) or 0.0) - (volume_strengths.get(window) or 0.0)) >= 1.20 for window in WINDOWS) >= 2)
    all_good = all(quality.get(window) == "good" for window in WINDOWS)
    buy += int(all_good and not any((value or 0.0) >= 45.0 for value in dist_values.values()))
    sell += int(all_good and not any((value or 0.0) >= 45.0 for value in acc_values.values()))
    return min(buy, 8), min(sell, 8)


def _weighted_relative_pct(
    price_changes: Mapping[int, float | None], btc_price_changes: Mapping[int, float | None]
) -> float | None:
    values = []
    for window in WINDOWS:
        coin = price_changes.get(window)
        btc = btc_price_changes.get(window)
        if coin is not None and btc is not None:
            values.append((coin - btc, WINDOW_WEIGHTS[window]))
    if len(values) < 2:
        return None
    total = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / total


def pre_anomaly_score(current: Mapping[str, Any], btc: Mapping[str, Any]) -> float:
    """Fresh map-only preselection balancing movement, relative movement and turnover."""
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
    turnover = volume / cap * 100.0 if cap > 0 else 0.0
    stable_activity = min(turnover, 35.0) * (1.25 if abs(hour) < 0.6 else 0.75)
    return (
        abs(hour) * 6.0
        + abs(rel_hour) * 4.0
        + abs(day) * 1.0
        + abs(rel_day) * 0.8
        + abs(week) * 0.15
        + abs(rel_week) * 0.12
        + stable_activity * 0.18
    )


def build_short_metrics(
    *,
    current: Mapping[str, Any],
    short_history: list[PricePoint],
    now_ms: int,
    btc_price_changes: Mapping[int, float | None] | None,
    config: Mapping[str, Any],
    is_reference: bool,
    btc_short: ShortMetrics | None = None,
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
    price_baselines, volume_baselines = _window_baselines(short_history, config)
    window_quality, quality_reasons, quality = _window_data_quality(
        current_volume=current_volume,
        price_changes=price_changes,
        volume_changes=volume_changes,
        volume_baselines=volume_baselines,
        minimum_volume=float(config.get("minimum_reliable_volume_usd", 500_000)),
        maximum_volume_jump_pct=float(config.get("maximum_plausible_volume_jump_pct", 1500.0)),
    )
    price_strengths = {
        window: _robust_signed_strength(price_changes.get(window), price_baselines[window], _thresholds(config, "price", window))
        for window in WINDOWS
    }
    volume_strengths = {
        window: _robust_signed_strength(volume_changes.get(window), volume_baselines[window], _thresholds(config, "volume", window))
        for window in WINDOWS
    }
    volume_colors = {
        window: _volume_color(
            volume_changes.get(window),
            volume_strengths.get(window),
            volume_baselines[window],
            window_quality[window],
        )
        for window in WINDOWS
    }

    accumulation: dict[int, float | None] = {}
    distribution: dict[int, float | None] = {}
    window_pressures: dict[int, float | None] = {}
    for window in WINDOWS:
        acc, dist, pressure = _window_pattern(
            price_change=price_changes.get(window),
            volume_change=volume_changes.get(window),
            price_strength=price_strengths.get(window),
            volume_strength=volume_strengths.get(window),
            window=window,
            config=config,
            quality=window_quality[window],
        )
        accumulation[window] = acc
        distribution[window] = dist
        window_pressures[window] = pressure

    acc_score, dist_score, acc_acceleration, dist_acceleration = _pattern_aggregate(accumulation, distribution)
    good_windows = sum(window_quality.get(window) == "good" for window in WINDOWS)
    strict_acc = _strict_accumulation_extreme(
        accumulation,
        distribution,
        price_changes,
        volume_strengths,
        config,
        window_quality,
        acc_score,
    )
    strict_dist = _strict_distribution_extreme(
        accumulation,
        distribution,
        price_strengths,
        volume_strengths,
        window_quality,
        dist_score,
    )

    relative_scores: dict[int, float | None] = {}
    if is_reference:
        relative_scores = {window: price_strengths.get(window) for window in WINDOWS}
        relative_short = 0.0
        relative_color = _relative_color(relative_scores, reference=True)
    else:
        for window in WINDOWS:
            coin_change = price_changes.get(window)
            btc_change = (btc_price_changes or {}).get(window)
            if coin_change is None or btc_change is None:
                relative_scores[window] = None
                continue
            coin_baseline = price_baselines[window]
            btc_baseline = btc_short.price_baselines[window] if btc_short else RobustBaseline(0.0, _thresholds(config, "price", window)[0], 0)
            center = coin_baseline.median - btc_baseline.median
            scale = math.sqrt(coin_baseline.scale**2 + btc_baseline.scale**2)
            relative_scores[window] = _clamp((coin_change - btc_change - center) / max(scale, 1e-9), -5.0, 5.0)
        relative_short = _weighted_relative_pct(price_changes, btc_price_changes or {})
        relative_color = _relative_color(relative_scores)

    pressure_values = [value for value in window_pressures.values() if value is not None]
    if len(pressure_values) >= 2:
        pressure_score = _weighted(window_pressures)
        same_direction = sum(value > 0.7 for value in pressure_values) >= 2 or sum(value < -0.7 for value in pressure_values) >= 2
        if same_direction:
            pressure_score += 0.18 * (1 if pressure_score > 0 else -1)
        pressure_score = _clamp(pressure_score, -3.25, 3.25)
    else:
        pressure_score = None
    p_color = _pressure_color(
        pressure_score,
        window_pressures,
        strict_accumulation=strict_acc,
        strict_distribution=strict_dist,
        quality=quality,
    )

    buy_count, sell_count = _condition_counts(
        accumulation=accumulation,
        distribution=distribution,
        acc_acceleration=acc_acceleration,
        dist_acceleration=dist_acceleration,
        relative_color=relative_color,
        volume_strengths=volume_strengths,
        price_strengths=price_strengths,
        quality=window_quality,
    )
    direction = "▲" if acc_score > dist_score else ("▼" if dist_score > acc_score else "=")
    signal = _pattern_color(
        accumulation_score=acc_score,
        distribution_score=dist_score,
        strict_accumulation=strict_acc,
        strict_distribution=strict_dist,
        quality=quality,
        good_windows=good_windows,
    )
    quality_factor = _quality_factor(quality, good_windows)
    proximity = max(acc_score, dist_score) * quality_factor
    margin = abs(acc_score - dist_score)
    pattern_confidence = _clamp((0.55 * proximity + 0.45 * margin) / 100.0, 0.0, 1.0)
    acceleration = acc_acceleration if acc_score >= dist_score else -dist_acceleration
    anomaly = proximity + max(buy_count, sell_count) * 3.2 + margin * 0.20 + abs(acceleration) * 0.10

    return ShortMetrics(
        price_changes=price_changes,
        volume_changes=volume_changes,
        volume_colors=volume_colors,
        relative_short_pct=relative_short,
        relative_color=relative_color,
        pressure_score=pressure_score,
        pressure_color=p_color,
        buy_count=buy_count,
        sell_count=sell_count,
        direction=direction,
        signal_color=signal,
        anomaly_score=anomaly,
        data_quality=quality,
        window_quality=window_quality,
        quality_reasons=quality_reasons,
        window_setup_scores=window_pressures,
        agreement_score=(acc_score - dist_score) / 33.333,
        accumulation_windows=accumulation,
        distribution_windows=distribution,
        accumulation_score=acc_score,
        distribution_score=dist_score,
        extreme_proximity=proximity,
        pattern_confidence=pattern_confidence,
        acceleration_score=acceleration,
        relative_window_scores=relative_scores,
        price_baselines=price_baselines,
        volume_baselines=volume_baselines,
        price_strengths=price_strengths,
        volume_strengths=volume_strengths,
    )


def _return_observations(points: list[PricePoint], timezone: str, block_hours: int) -> tuple[list[TimeObservation], float | None]:
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
    lower = max(5 / 60, median_interval * 0.25)
    upper = min(72.0, median_interval * 4.0)
    raw: list[tuple[int, int, int | None, float, float | None]] = []
    tz = ZoneInfo(timezone)
    for previous, current in zip(points, points[1:]):
        elapsed_hours = (current.timestamp_ms - previous.timestamp_ms) / 3_600_000
        if elapsed_hours < lower or elapsed_hours > upper or previous.rate <= 0:
            continue
        price_change = (current.rate / previous.rate - 1.0) * 100.0 / max(math.sqrt(elapsed_hours), 1.0)
        volume_change: float | None = None
        if previous.volume is not None and current.volume is not None and previous.volume > 0:
            candidate = (current.volume / previous.volume - 1.0) * 100.0
            if math.isfinite(candidate) and abs(candidate) <= 1500:
                volume_change = candidate / max(math.sqrt(elapsed_hours), 1.0)
        local = datetime.fromtimestamp(current.timestamp_ms / 1000, tz=tz)
        block = (local.hour // block_hours) * block_hours if median_interval <= 8.0 else None
        raw.append((current.timestamp_ms, local.weekday(), block, price_change, volume_change))
    if not raw:
        return [], median_interval
    price_scale = _robust_baseline([item[3] for item in raw], 0.01)
    volume_scale = _robust_baseline([item[4] for item in raw if item[4] is not None], 0.10)
    observations: list[TimeObservation] = []
    combined_values: list[float] = []
    for timestamp, weekday, block, price_value, volume_value in raw:
        p = _clamp((price_value - price_scale.median) / price_scale.scale, -3.5, 3.5)
        if volume_value is None:
            score = p * 0.55
        else:
            v = _clamp((volume_value - volume_scale.median) / volume_scale.scale, -3.5, 3.5)
            # Weekday quality rewards positive price with confirming volume and stable price with demand.
            if abs(p) <= 0.65 and v > 0:
                score = 0.72 * v
            elif p > 0 and v >= 0:
                score = 0.62 * p + 0.38 * v
            elif p > 0 and v < 0:
                score = 0.55 * p + 0.45 * v
            elif p < 0 and v > 0:
                score = 0.70 * p - 0.30 * v
            else:
                score = 0.72 * p + 0.28 * v
        combined_values.append(score)
        observations.append(TimeObservation(timestamp, weekday, block, score))
    baseline = 0.60 * statistics.median(combined_values) + 0.40 * _trimmed_mean(combined_values)
    centered = [TimeObservation(item.timestamp_ms, item.weekday, item.block, item.score - baseline) for item in observations]
    # Keep recurring weekday extremes; robust medians/weighted checks below handle isolated outliers.
    return centered, median_interval


def _recency_weight(timestamp_ms: int, now_ms: int, half_life_days: float = 35.0) -> float:
    age_days = max(0.0, (now_ms - timestamp_ms) / 86_400_000)
    return 0.5 ** (age_days / max(half_life_days, 1.0))


def _day_metrics(items: list[TimeObservation], now_ms: int) -> tuple[float, float, float, float]:
    weighted = [(item.score, _recency_weight(item.timestamp_ms, now_ms)) for item in items]
    median_value = _weighted_median(weighted)
    weighted_mean = sum(value * weight for value, weight in weighted) / max(sum(weight for _, weight in weighted), 1e-9)
    central = 0.65 * median_value + 0.35 * weighted_mean
    hit_rate = sum(weight for value, weight in weighted if value > 0) / max(sum(weight for _, weight in weighted), 1e-9)
    dispersion = _robust_baseline([item.score for item in items], 0.10).scale
    consistency = _clamp(abs(hit_rate - 0.5) * 2.0, 0.0, 1.0)
    return central, hit_rate, dispersion, consistency


def _time_summary(values: list[TimeObservation], now_ms: int, min_samples: int) -> tuple[str, float, float]:
    if len(values) < min_samples:
        return "?", 0.0, min(1.0, len(values) / max(min_samples, 1))
    central, hit_rate, dispersion, consistency = _day_metrics(values, now_ms)
    sample_confidence = min(1.0, len(values) / max(min_samples * 1.5, 1.0))
    confidence = sample_confidence * (0.55 + 0.45 * consistency) * (1.0 / (1.0 + 0.12 * dispersion))
    if confidence < 0.44:
        return "?", central, confidence
    if central >= 1.0 and hit_rate >= 0.68 and confidence >= 0.62:
        return "++", central, confidence
    if central >= 0.30 and hit_rate >= 0.58 and confidence >= 0.48:
        return "+", central, confidence
    if central <= -1.0 and hit_rate <= 0.32 and confidence >= 0.62:
        return "--", central, confidence
    if central <= -0.30 and hit_rate <= 0.42 and confidence >= 0.48:
        return "-", central, confidence
    return "=", central, confidence


def analyze_seasonality(
    points: list[PricePoint],
    now: datetime,
    timezone: str,
    block_hours: int = 4,
    min_samples: int = 12,
    minimum_observations: int = 60,
) -> Seasonality:
    observations, _ = _return_observations(points, timezone, block_hours)
    if len(observations) < minimum_observations:
        return Seasonality("?", tuple(), len(observations), "insufficient")
    now_ms = int(now.timestamp() * 1000)
    recent_cutoff = now_ms - 45 * 86_400_000
    by_weekday: dict[int, list[TimeObservation]] = {}
    by_slot: dict[tuple[int, int], list[TimeObservation]] = {}
    for item in observations:
        by_weekday.setdefault(item.weekday, []).append(item)
        if item.block is not None:
            by_slot.setdefault((item.weekday, item.block), []).append(item)

    candidates: list[tuple[int, float, float]] = []
    weekday_scores: dict[str, float] = {}
    weekday_confidence: dict[str, float] = {}
    required = max(12, min_samples)
    for weekday, items in by_weekday.items():
        recent = [item for item in items if item.timestamp_ms >= recent_cutoff]
        if len(items) < required or len(recent) < 4:
            continue
        full_central, full_hit, full_dispersion, full_consistency = _day_metrics(items, now_ms)
        recent_central, recent_hit, _, recent_consistency = _day_metrics(recent, now_ms)
        same_positive = full_central > 0 and recent_central > 0
        sample_factor = min(1.0, len(items) / 18.0)
        confidence = sample_factor * (0.40 + 0.30 * full_consistency + 0.30 * recent_consistency)
        confidence *= 1.0 / (1.0 + 0.10 * full_dispersion)
        conservative = min(full_central, recent_central)
        quality = conservative * confidence * (0.55 + 0.25 * full_hit + 0.20 * recent_hit)
        name = DAY_NAMES[weekday]
        weekday_scores[name] = round(quality, 4)
        weekday_confidence[name] = round(confidence, 4)
        if same_positive and full_hit >= 0.55 and recent_hit >= 0.55 and confidence >= 0.48 and quality > 0.08:
            candidates.append((weekday, quality, confidence))
    candidates.sort(key=lambda item: (item[1], item[2]), reverse=True)
    selected = [weekday for weekday, _, _ in candidates[:2]]
    selected.sort(key=DISPLAY_WEEK_ORDER.index)
    best_days = tuple(DAY_NAMES[weekday] for weekday in selected)

    local_now = now.astimezone(ZoneInfo(timezone))
    current_block = (local_now.hour // block_hours) * block_hours
    slot = by_slot.get((local_now.weekday(), current_block), [])
    if len(slot) >= max(12, min_samples):
        current, score, confidence = _time_summary(slot, now_ms, max(12, min_samples))
        source = "weekday-block"
    else:
        day = by_weekday.get(local_now.weekday(), [])
        current, score, confidence = _time_summary(day, now_ms, max(12, min_samples))
        source = "weekday" if day else "insufficient"
    return Seasonality(
        current=current,
        best_weekdays=best_days,
        samples=len(observations),
        source=source,
        current_score=score,
        current_confidence=confidence,
        weekday_scores=weekday_scores,
        weekday_confidence=weekday_confidence,
    )


def _rolling_week_returns(points: list[PricePoint]) -> list[float]:
    if len(points) < 10:
        return []
    results: list[float] = []
    last_day: int | None = None
    for current in points:
        day = current.timestamp_ms // 86_400_000
        if day == last_day:
            continue
        previous = _nearest_point(points, current.timestamp_ms - 7 * 86_400_000, int(36 * 3_600_000))
        if previous and previous.timestamp_ms < current.timestamp_ms:
            value = _pct(current.rate, previous.rate)
            if value is not None and math.isfinite(value):
                results.append(value)
                last_day = day
    return results


def _week_context(week_pct: float, points: list[PricePoint]) -> tuple[str, float | None, float]:
    samples = _rolling_week_returns(points)
    if len(samples) < 12:
        # Conservative fallback: no extreme colors without enough own-history samples.
        if week_pct >= 4.0:
            return GREEN, None, min(0.45, len(samples) / 12.0)
        if week_pct >= 0.8:
            return BLUE, None, min(0.45, len(samples) / 12.0)
        if week_pct <= -4.0:
            return ORANGE, None, min(0.45, len(samples) / 12.0)
        return YELLOW, None, min(0.45, len(samples) / 12.0)
    baseline = _robust_baseline(samples, 0.40)
    z = _robust_z(week_pct, baseline) or 0.0
    percentile = sum(value <= week_pct for value in samples) / len(samples)
    confidence = min(1.0, len(samples) / 40.0)
    if confidence >= 0.55 and percentile >= 0.95 and z >= 2.0 and week_pct > 0:
        return PURPLE, percentile, confidence
    if confidence >= 0.55 and percentile <= 0.05 and z <= -2.0 and week_pct < 0:
        return RED, percentile, confidence
    if percentile >= 0.78 and week_pct > 0:
        return GREEN, percentile, confidence
    if percentile >= 0.55 and week_pct > 0:
        return BLUE, percentile, confidence
    if percentile <= 0.22 and week_pct < 0:
        return ORANGE, percentile, confidence
    if percentile <= 0.45 and week_pct < 0:
        return ORANGE, percentile, confidence
    return YELLOW, percentile, confidence


def time_color(mark: str) -> str:
    return {"++": PURPLE, "+": GREEN, "=": YELLOW, "-": ORANGE, "--": RED, "?": BROWN}.get(mark, BROWN)


def week_color(week_pct: float) -> str:
    """Compatibility fallback used by external tests."""
    if week_pct >= 10.0:
        return PURPLE
    if week_pct >= 3.0:
        return GREEN
    if week_pct >= 0.75:
        return BLUE
    if week_pct <= -10.0:
        return RED
    if week_pct <= -0.75:
        return ORANGE
    return YELLOW


def current_now_signal(
    short: ShortMetrics,
    seasonality: Seasonality,
    config: Mapping[str, Any],
    *,
    is_reference: bool,
    week_signal: str = YELLOW,
) -> tuple[float | None, str]:
    if short.data_quality == "insufficient":
        return None, WHITE
    valid = [value for value in short.window_setup_scores.values() if value is not None]
    if len(valid) < 2:
        return None, BROWN if short.data_quality == "uncertain" else WHITE
    pattern_axis = (short.accumulation_score - short.distribution_score) / 33.333
    pressure = short.pressure_score or 0.0
    relative = color_level(short.relative_color) / 2.0
    week = color_level(week_signal) / 3.0
    historical = 0.0
    if seasonality.current_score is not None and seasonality.current_confidence >= 0.48:
        historical = _clamp(seasonality.current_score / 1.2, -1.0, 1.0) * seasonality.current_confidence
    score = 0.52 * pattern_axis + 0.25 * pressure + (0.0 if is_reference else 0.14 * relative) + 0.05 * week + 0.04 * historical

    three_good = all(short.window_quality.get(window) == "good" for window in WINDOWS)
    positive_confirmations = sum(
        [short.signal_color == PURPLE, short.pressure_color == PURPLE, short.relative_color in {GREEN, PURPLE}, week_signal in {GREEN, PURPLE}]
    )
    negative_confirmations = sum(
        [short.signal_color == RED, short.pressure_color == RED, short.relative_color in {ORANGE, RED}, week_signal in {ORANGE, RED}]
    )
    purple = three_good and positive_confirmations >= 3 and score >= 2.0
    red = three_good and negative_confirmations >= 3 and score <= -2.0
    color = _strict_gradient_color(score, extreme_positive=purple, extreme_negative=red)
    if short.data_quality == "uncertain" and color in {PURPLE, RED}:
        color = GREEN if score > 0 else ORANGE
    return score, color


def btc_gate(short: ShortMetrics, config: Mapping[str, Any]) -> bool:
    return short.signal_color in {BLUE, GREEN, PURPLE} and short.data_quality == "good"


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
    week_signal, percentile, week_confidence = _week_context(week_pct, history)
    now_score, now_color = current_now_signal(
        short,
        seasonality,
        config,
        is_reference=is_reference,
        week_signal=week_signal,
    )
    # Keep X/8 focused on the two requested short-term extremes; long-term fields do not inflate it.
    if is_reference:
        short.relative_color = _relative_color(short.price_strengths, reference=True)
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
        week_percentile=percentile,
        week_confidence=week_confidence,
    )


def strength_count(item: CoinAnalysis) -> int:
    return max(item.short.buy_count, item.short.sell_count)


def confidence_sort_key(item: CoinAnalysis) -> tuple[float, ...]:
    quality_rank = {"good": 2.0, "uncertain": 1.0, "insufficient": 0.0}.get(item.short.data_quality, 0.0)
    count = strength_count(item)
    margin = abs(item.short.accumulation_score - item.short.distribution_score)
    # Primary order: closest to either strict extreme, then data quality and confirmed criteria.
    return (
        float(item.short.extreme_proximity),
        float(item.short.pattern_confidence),
        quality_rank,
        float(count),
        float(margin),
        float(abs(item.short.acceleration_score)),
        float(item.short.anomaly_score),
    )


def format_line(item: CoinAnalysis, *, generated_at: datetime, timezone: str) -> str:
    volumes = "".join(item.short.volume_colors.get(window, WHITE) for window in WINDOWS)
    count = strength_count(item)
    weekdays = "".join(item.seasonality.best_weekdays[:2])
    common = (
        f"{item.short.signal_color}{count}{item.short.direction}"
        f"7{item.week_color}B{item.short.relative_color}"
        f"P{item.short.pressure_color}V{volumes}N{item.now_color}{weekdays}"
    )
    if item.is_reference:
        minute = generated_at.astimezone(ZoneInfo(timezone)).strftime(":%M")
        return common + minute
    return common + display_code(item.display_code)


def build_report(
    reference: CoinAnalysis,
    top_coins: list[CoinAnalysis],
    *,
    generated_at: datetime,
    timezone: str,
) -> str:
    ordered = sorted(top_coins, key=confidence_sort_key, reverse=True)
    return "\n".join(
        [format_line(reference, generated_at=generated_at, timezone=timezone)]
        + [format_line(item, generated_at=generated_at, timezone=timezone) for item in ordered]
    )


def analysis_to_dict(item: CoinAnalysis) -> dict[str, Any]:
    return asdict(item)
