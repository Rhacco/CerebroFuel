"""Persistence-aware crypto extreme analysis with stable daily context (v3.2.6 reliable-cache refresh)."""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
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
    price_continuity: dict[int, float] = field(default_factory=dict)
    volume_continuity: dict[int, float] = field(default_factory=dict)
    price_jump_share: dict[int, float] = field(default_factory=dict)
    volume_jump_share: dict[int, float] = field(default_factory=dict)
    temporal_axes: dict[int, float | None] = field(default_factory=dict)
    temporal_pressures: dict[int, float | None] = field(default_factory=dict)
    temporal_score: float = 0.0
    temporal_pressure: float = 0.0
    positive_streak: int = 0
    negative_streak: int = 0
    temporal_samples: int = 0
    reversal_guard: bool = False
    flash_score: float = 0.0
    flash_direction: str = "="


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
    flash_score: float = 0.0
    ranking_score: float = 0.0


@dataclass(frozen=True)
class TimeObservation:
    timestamp_ms: int
    weekday: int
    block: int | None
    score: float


@dataclass(frozen=True)
class DailyObservation:
    timestamp_ms: int
    date_ordinal: int
    weekday: int
    score: float
    price_pct: float
    volume_pct: float | None
    reliability: float


@dataclass
class TemporalContext:
    axes: dict[int, float | None] = field(default_factory=dict)
    pressures: dict[int, float | None] = field(default_factory=dict)
    smoothed_axis: float = 0.0
    smoothed_pressure: float = 0.0
    positive_streak: int = 0
    negative_streak: int = 0
    positive_votes: int = 0
    negative_votes: int = 0
    recent_positive: bool = False
    recent_negative: bool = False
    positive_confirmed: bool = False
    negative_confirmed: bool = False
    reversal_guard: bool = False
    consensus: float = 0.0
    opposite_count: int = 0
    samples: int = 0


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



def _series_with_current(
    history: Sequence[PricePoint],
    *,
    now_ms: int,
    rate: float,
    volume: float | None,
) -> list[PricePoint]:
    """Return a sorted series with the fresh map value as the final observation."""
    by_timestamp = {point.timestamp_ms: point for point in history if point.timestamp_ms <= now_ms}
    by_timestamp[now_ms] = PricePoint(now_ms, rate, volume)
    return sorted(by_timestamp.values(), key=lambda point: point.timestamp_ms)


def _path_stats(
    series: Sequence[PricePoint],
    *,
    now_ms: int,
    window: int,
    field_name: str,
) -> tuple[float, float, int]:
    """Measure whether a change is sustained or concentrated in one staircase jump."""
    start_ms = now_ms - window * 60_000
    points = [point for point in series if start_ms - 180_000 <= point.timestamp_ms <= now_ms + 60_000]
    if len(points) < 3:
        return 0.0, 1.0, max(0, len(points) - 1)
    changes: list[float] = []
    for previous, current in zip(points, points[1:]):
        previous_value = getattr(previous, field_name)
        current_value = getattr(current, field_name)
        change = _pct(current_value, previous_value)
        if change is not None and math.isfinite(change):
            changes.append(change)
    if len(changes) < 2:
        return 0.0, 1.0, len(changes)
    total = sum(changes)
    absolute_total = sum(abs(value) for value in changes)
    if absolute_total <= 1e-12:
        return 0.45, 0.0, len(changes)
    direction = 1.0 if total > 0 else (-1.0 if total < 0 else 0.0)
    meaningful = [value for value in changes if abs(value) >= max(0.002, absolute_total * 0.015)]
    if not meaningful:
        meaningful = changes
    same_weight = sum(abs(value) for value in meaningful if direction == 0.0 or value * direction > 0)
    directional_share = same_weight / max(sum(abs(value) for value in meaningful), 1e-12)
    jump_share = max(abs(value) for value in changes) / absolute_total
    expected_steps = max(2.0, window / 5.0)
    coverage = min(1.0, len(changes) / expected_steps)
    continuity = coverage * (0.67 * directional_share + 0.33 * (1.0 - jump_share))
    return _clamp(continuity, 0.0, 1.0), _clamp(jump_share, 0.0, 1.0), len(changes)


def _all_path_stats(
    series: Sequence[PricePoint], now_ms: int
) -> tuple[dict[int, float], dict[int, float], dict[int, float], dict[int, float]]:
    price_continuity: dict[int, float] = {}
    volume_continuity: dict[int, float] = {}
    price_jump_share: dict[int, float] = {}
    volume_jump_share: dict[int, float] = {}
    for window in WINDOWS:
        p_cont, p_jump, _ = _path_stats(series, now_ms=now_ms, window=window, field_name="rate")
        v_cont, v_jump, _ = _path_stats(series, now_ms=now_ms, window=window, field_name="volume")
        price_continuity[window] = p_cont
        volume_continuity[window] = v_cont
        price_jump_share[window] = p_jump
        volume_jump_share[window] = v_jump
    return price_continuity, volume_continuity, price_jump_share, volume_jump_share

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
    *,
    continuity: float = 0.0,
    jump_share: float = 1.0,
) -> str:
    if quality == "insufficient" or value is None or strength is None:
        return WHITE
    if quality == "uncertain":
        return BROWN
    z = _robust_z(value, baseline) or 0.0
    sustained = continuity >= 0.62 and jump_share <= 0.68
    extreme_positive = (
        sustained and baseline.samples >= 8 and value > 0 and strength >= 2.85 and z >= 2.8
    )
    extreme_negative = (
        sustained and baseline.samples >= 8 and value < 0 and strength <= -2.85 and z <= -2.8
    )
    adjusted = strength
    if jump_share > 0.76:
        adjusted *= 0.48
    elif continuity < 0.45:
        adjusted *= 0.68
    color = _strict_gradient_color(
        adjusted,
        extreme_positive=extreme_positive,
        extreme_negative=extreme_negative,
    )
    if jump_share > 0.76 and color in {PURPLE, GREEN}:
        return BLUE
    if continuity < 0.45 and color == GREEN:
        return BLUE
    return color


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
    price_continuity: float = 0.0,
    volume_continuity: float = 0.0,
    price_jump_share: float = 1.0,
    volume_jump_share: float = 1.0,
) -> tuple[float | None, float | None, float | None]:
    """Return accumulation, distribution and pressure for one sustained window."""
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
    volume_path = 0.48 + 0.52 * volume_continuity
    if volume_jump_share > 0.76:
        volume_path *= 0.52
    elif volume_jump_share > 0.64:
        volume_path *= 0.76
    accumulation = 100.0 * volume_force * (0.82 * price_stability + 0.18 * slight_up) * no_drop
    accumulation *= (0.72 + 0.28 * lead_force) * volume_path

    price_rise = _clamp((price_strength - 0.45) / 2.55, 0.0, 1.0)
    lag = price_strength - volume_strength
    lag_force = _clamp((lag - 0.65) / 2.35, 0.0, 1.0)
    nonconfirm = _clamp((0.35 - volume_strength) / 2.35, 0.0, 1.0)
    price_path = 0.52 + 0.48 * price_continuity
    if price_jump_share > 0.78:
        price_path *= 0.72
    volume_nonconfirm_path = 0.72 + 0.28 * (volume_continuity if volume_strength < 0 else 0.55)
    distribution = 100.0 * price_rise * (0.72 * lag_force + 0.28 * nonconfirm)
    distribution *= price_path * volume_nonconfirm_path

    confirmed_selling = (
        _clamp((-price_strength - 0.35) / 2.40, 0.0, 1.0)
        * _clamp((volume_strength - 0.35) / 2.40, 0.0, 1.0)
        * (0.48 + 0.52 * min(price_continuity, volume_continuity))
    )
    fading_demand = (
        _clamp((-price_strength - 0.30) / 2.70, 0.0, 1.0)
        * _clamp((-volume_strength - 0.30) / 2.70, 0.0, 1.0)
        * (0.55 + 0.45 * min(price_continuity, volume_continuity))
    )
    positive_pressure = accumulation / 100.0
    negative_pressure = max(distribution / 100.0, confirmed_selling, 0.55 * fading_demand)
    pressure = _clamp((positive_pressure - negative_pressure) * 3.25, -3.25, 3.25)
    return accumulation, distribution, pressure



def _snapshot_signature(
    series: Sequence[PricePoint],
    *,
    as_of_ms: int,
    config: Mapping[str, Any],
) -> tuple[float, float] | None:
    endpoint = _nearest_point(series, as_of_ms, 4 * 60_000)
    if endpoint is None:
        return None
    subset = [point for point in series if point.timestamp_ms <= endpoint.timestamp_ms]
    if len(subset) < int(config.get("minimum_short_history_points", 8)):
        return None
    price_changes, volume_changes = compute_window_changes_from_history(
        current_rate=endpoint.rate,
        current_volume=endpoint.volume,
        history=subset,
        now_ms=endpoint.timestamp_ms,
    )
    price_baselines, volume_baselines = _window_baselines(subset, config)
    quality, _, overall = _window_data_quality(
        current_volume=endpoint.volume,
        price_changes=price_changes,
        volume_changes=volume_changes,
        volume_baselines=volume_baselines,
        minimum_volume=float(config.get("minimum_reliable_volume_usd", 500_000)),
        maximum_volume_jump_pct=float(config.get("maximum_plausible_volume_jump_pct", 1500.0)),
    )
    if overall == "insufficient":
        return None
    p_cont, v_cont, p_jump, v_jump = _all_path_stats(subset, endpoint.timestamp_ms)
    accumulations: dict[int, float | None] = {}
    distributions: dict[int, float | None] = {}
    pressures: dict[int, float | None] = {}
    for window in WINDOWS:
        p_strength = _robust_signed_strength(
            price_changes.get(window), price_baselines[window], _thresholds(config, "price", window)
        )
        v_strength = _robust_signed_strength(
            volume_changes.get(window), volume_baselines[window], _thresholds(config, "volume", window)
        )
        acc, dist, pressure = _window_pattern(
            price_change=price_changes.get(window),
            volume_change=volume_changes.get(window),
            price_strength=p_strength,
            volume_strength=v_strength,
            window=window,
            config=config,
            quality=quality[window],
            price_continuity=p_cont[window],
            volume_continuity=v_cont[window],
            price_jump_share=p_jump[window],
            volume_jump_share=v_jump[window],
        )
        accumulations[window] = acc
        distributions[window] = dist
        pressures[window] = pressure
    acc_score, dist_score, _, _ = _pattern_aggregate(accumulations, distributions)
    usable_pressures = [value for value in pressures.values() if value is not None]
    pressure_score = _weighted(pressures) if len(usable_pressures) >= 2 else 0.0
    axis = _clamp((acc_score - dist_score) / 100.0, -1.0, 1.0)
    return axis, _clamp(pressure_score / 3.25, -1.0, 1.0)


def _streak(values: Sequence[float | None], *, positive: bool, threshold: float) -> int:
    count = 0
    for value in values:
        if value is None:
            break
        if positive and value >= threshold:
            count += 1
        elif not positive and value <= -threshold:
            count += 1
        else:
            break
    return count


def _temporal_context(
    series: Sequence[PricePoint],
    *,
    now_ms: int,
    current_axis: float,
    current_pressure: float,
    config: Mapping[str, Any],
) -> TemporalContext:
    """Reconstruct recent states and apply hysteresis without external state.

    Strong colors need a persistent run. A fresh opposite impulse is treated as a
    possible transition, not as an immediate reversal.
    """
    offsets = tuple(int(value) for value in config.get("temporal_offsets_minutes", [5, 10, 15, 20, 25, 30]))
    axes: dict[int, float | None] = {}
    pressures: dict[int, float | None] = {}
    for offset in offsets:
        snapshot = _snapshot_signature(series, as_of_ms=now_ms - offset * 60_000, config=config)
        axes[offset] = snapshot[0] if snapshot else None
        pressures[offset] = snapshot[1] if snapshot else None

    sequence = [current_axis] + [axes[offset] for offset in offsets]
    usable = [float(value) for value in sequence if value is not None]
    clear = float(config.get("temporal_axis_clear", 0.20))
    strong = float(config.get("temporal_axis_strong", 0.42))
    guard_threshold = float(config.get("temporal_guard_threshold", clear * 0.70))
    min_confirmations = int(config.get("temporal_confirmation_points", 4))

    positive_streak = _streak(sequence, positive=True, threshold=clear)
    negative_streak = _streak(sequence, positive=False, threshold=clear)
    positive_votes = sum(value >= clear for value in usable)
    negative_votes = sum(value <= -clear for value in usable)
    neutral_votes = len(usable) - positive_votes - negative_votes
    consensus = abs(positive_votes - negative_votes) / max(len(usable), 1)

    recent20 = [axes[offset] for offset in offsets if offset <= 20 and axes[offset] is not None]
    recent30 = [axes[offset] for offset in offsets if offset <= 30 and axes[offset] is not None]
    recent_positive = any(value >= guard_threshold for value in recent30)
    recent_negative = any(value <= -guard_threshold for value in recent30)
    current_positive = current_axis >= clear
    current_negative = current_axis <= -clear

    positive_confirmed = (
        positive_streak >= min_confirmations
        and positive_votes >= min_confirmations
        and not any(value <= -clear for value in recent20)
    )
    negative_confirmed = (
        negative_streak >= min_confirmations
        and negative_votes >= min_confirmations
        and not any(value >= clear for value in recent20)
    )
    opposite_count = (
        sum(value <= -clear for value in recent30) if current_positive
        else sum(value >= clear for value in recent30) if current_negative
        else min(positive_votes, negative_votes)
    )
    reversal_guard = (
        current_positive and recent_negative and not positive_confirmed
    ) or (
        current_negative and recent_positive and not negative_confirmed
    ) or (
        current_positive and any(value <= -guard_threshold for value in recent20)
    ) or (
        current_negative and any(value >= guard_threshold for value in recent20)
    )

    # Current data matters, but a robust median prevents one five-minute point
    # from dominating the whole state.
    weights = [0.25, 0.20, 0.16, 0.13, 0.10, 0.07, 0.05, 0.04]
    weighted_axes: list[tuple[float, float]] = []
    weighted_pressures: list[tuple[float, float]] = []
    for index, value in enumerate(sequence):
        if value is not None:
            weighted_axes.append((float(value), weights[min(index, len(weights) - 1)]))
    pressure_sequence = [current_pressure] + [pressures[offset] for offset in offsets]
    for index, value in enumerate(pressure_sequence):
        if value is not None:
            weighted_pressures.append((float(value), weights[min(index, len(weights) - 1)]))

    def robust_temporal(values: list[tuple[float, float]]) -> float:
        if not values:
            return 0.0
        total = sum(weight for _, weight in values)
        mean = sum(value * weight for value, weight in values) / max(total, 1e-9)
        median = _weighted_median(values)
        return 0.48 * mean + 0.52 * median

    smoothed_axis = robust_temporal(weighted_axes)
    smoothed_pressure = robust_temporal(weighted_pressures)

    # Mixed votes shrink the score; an unresolved reversal is capped near neutral.
    agreement_factor = 0.55 + 0.45 * consensus
    smoothed_axis *= agreement_factor
    smoothed_pressure *= agreement_factor
    if reversal_guard:
        smoothed_axis = _clamp(smoothed_axis, -0.24, 0.24)
        smoothed_pressure = _clamp(smoothed_pressure, -0.30, 0.30)
    elif neutral_votes >= 3 and consensus < 0.35:
        smoothed_axis *= 0.72
        smoothed_pressure *= 0.78

    return TemporalContext(
        axes=axes,
        pressures=pressures,
        smoothed_axis=_clamp(smoothed_axis, -1.0, 1.0),
        smoothed_pressure=_clamp(smoothed_pressure, -1.0, 1.0),
        positive_streak=positive_streak,
        negative_streak=negative_streak,
        positive_votes=positive_votes,
        negative_votes=negative_votes,
        recent_positive=recent_positive,
        recent_negative=recent_negative,
        positive_confirmed=positive_confirmed,
        negative_confirmed=negative_confirmed,
        reversal_guard=reversal_guard,
        consensus=consensus,
        opposite_count=opposite_count,
        samples=sum(value is not None for value in axes.values()),
    )

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
    volume_continuity: Mapping[int, float],
    volume_jump_share: Mapping[int, float],
    config: Mapping[str, Any],
    quality: Mapping[int, str],
    aggregate: float,
    temporal: TemporalContext,
) -> bool:
    if any(quality.get(window) != "good" for window in WINDOWS):
        return False
    if aggregate < 84.0 or not temporal.positive_confirmed or temporal.recent_negative:
        return False
    if temporal.smoothed_axis < 0.55 or temporal.positive_streak < int(config.get("temporal_confirmation_points", 4)):
        return False
    if sum((accumulation.get(window) or 0.0) >= 78.0 for window in WINDOWS) < 2:
        return False
    if any((distribution.get(window) or 0.0) >= 32.0 for window in WINDOWS):
        return False
    if sum(volume_continuity.get(window, 0.0) >= 0.62 for window in WINDOWS) < 2:
        return False
    if sum(volume_jump_share.get(window, 1.0) <= 0.68 for window in WINDOWS) < 2:
        return False
    for window in WINDOWS:
        p_light, p_clear, _ = _thresholds(config, "price", window)
        price = price_changes.get(window)
        volume_strength = volume_strengths.get(window)
        if price is None or volume_strength is None:
            return False
        if price < -p_light or price > p_clear * 1.15 or volume_strength < 1.65:
            return False
    return True


def _strict_distribution_extreme(
    accumulation: Mapping[int, float | None],
    distribution: Mapping[int, float | None],
    price_strengths: Mapping[int, float | None],
    volume_strengths: Mapping[int, float | None],
    price_continuity: Mapping[int, float],
    config: Mapping[str, Any],
    quality: Mapping[int, str],
    aggregate: float,
    temporal: TemporalContext,
) -> bool:
    if any(quality.get(window) != "good" for window in WINDOWS):
        return False
    if aggregate < 84.0 or not temporal.negative_confirmed or temporal.recent_positive:
        return False
    if temporal.smoothed_axis > -0.55 or temporal.negative_streak < int(config.get("temporal_confirmation_points", 4)):
        return False
    if sum((distribution.get(window) or 0.0) >= 76.0 for window in WINDOWS) < 2:
        return False
    if any((accumulation.get(window) or 0.0) >= 32.0 for window in WINDOWS):
        return False
    if sum(price_continuity.get(window, 0.0) >= 0.54 for window in WINDOWS) < 2:
        return False
    for window in WINDOWS:
        price_strength = price_strengths.get(window)
        volume_strength = volume_strengths.get(window)
        if price_strength is None or volume_strength is None:
            return False
        if price_strength < 0.78 or price_strength - volume_strength < 1.00:
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
    temporal: TemporalContext,
) -> str:
    if good_windows < 2:
        return BROWN if quality == "uncertain" else WHITE
    if strict_accumulation:
        return PURPLE
    if strict_distribution:
        return RED
    axis = temporal.smoothed_axis
    if temporal.reversal_guard:
        if axis >= 0.16:
            return BLUE
        if axis <= -0.16:
            return ORANGE
        return YELLOW
    if axis >= 0.52 and temporal.positive_confirmed and temporal.consensus >= 0.55:
        return GREEN
    if axis >= 0.20 and temporal.positive_votes >= 3:
        return BLUE
    if axis <= -0.52 and temporal.negative_confirmed and temporal.consensus >= 0.55:
        return ORANGE
    if axis <= -0.20 and temporal.negative_votes >= 3:
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
    temporal: TemporalContext,
) -> str:
    values = [value for value in window_pressures.values() if value is not None]
    if len(values) < 2 or score is None:
        return BROWN if quality == "uncertain" else WHITE
    if (
        strict_accumulation
        and temporal.positive_confirmed
        and temporal.consensus >= 0.70
        and min(values) >= 1.25
        and sum(value >= 2.1 for value in values) >= 2
    ):
        return PURPLE
    if (
        strict_distribution
        and temporal.negative_confirmed
        and temporal.consensus >= 0.70
        and max(values) <= -0.45
        and score <= -2.0
    ):
        return RED
    if temporal.reversal_guard:
        if score >= 0.65:
            return BLUE
        if score <= -0.65:
            return ORANGE
        return YELLOW
    if score >= 1.35 and temporal.positive_confirmed and temporal.consensus >= 0.50:
        return GREEN
    if score >= 0.45 and temporal.positive_votes >= 3:
        return BLUE
    if score <= -1.25 and temporal.negative_confirmed and temporal.consensus >= 0.50:
        return ORANGE
    if score <= -0.45 and temporal.negative_votes >= 3:
        return ORANGE
    return YELLOW

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
    relative_color: str,
    quality: Mapping[int, str],
    price_continuity: Mapping[int, float],
    volume_continuity: Mapping[int, float],
    volume_jump_share: Mapping[int, float],
    temporal: TemporalContext,
) -> tuple[int, int]:
    """Count eight conservative confirmations, not eight correlated colors."""
    acc = {window: accumulation.get(window) or 0.0 for window in WINDOWS}
    dist = {window: distribution.get(window) or 0.0 for window in WINDOWS}
    all_good = all(quality.get(window) == "good" for window in WINDOWS)
    persistent_path = sum(
        volume_continuity.get(window, 0.0) >= 0.60
        and volume_jump_share.get(window, 1.0) <= 0.70
        for window in WINDOWS
    ) >= 2
    stable_price_path = sum(price_continuity.get(window, 0.0) >= 0.54 for window in WINDOWS) >= 2
    buy_conditions = [
        acc[10] >= 62.0,
        acc[20] >= 62.0,
        acc[60] >= 56.0,
        sum(value >= 58.0 for value in acc.values()) >= 2 and max(dist.values()) < 42.0,
        persistent_path,
        temporal.positive_confirmed and temporal.consensus >= 0.50,
        temporal.positive_votes >= 5 and not temporal.recent_negative and not temporal.reversal_guard,
        all_good and relative_color in {BLUE, GREEN, PURPLE},
    ]
    sell_conditions = [
        dist[10] >= 62.0,
        dist[20] >= 62.0,
        dist[60] >= 56.0,
        sum(value >= 58.0 for value in dist.values()) >= 2 and max(acc.values()) < 42.0,
        stable_price_path,
        temporal.negative_confirmed and temporal.consensus >= 0.50,
        temporal.negative_votes >= 5 and not temporal.recent_positive and not temporal.reversal_guard,
        all_good and relative_color in {ORANGE, RED},
    ]
    return sum(buy_conditions), sum(sell_conditions)


def _reference_confirmation_scores(
    price_strengths: Mapping[int, float | None],
    volume_strengths: Mapping[int, float | None],
    window_pressures: Mapping[int, float | None],
) -> dict[int, float | None]:
    """BTC B-field: own price move only counts when volume/pressure do not contradict it."""
    result: dict[int, float | None] = {}
    for window in WINDOWS:
        price = price_strengths.get(window)
        volume = volume_strengths.get(window)
        pressure = window_pressures.get(window)
        if price is None or volume is None:
            result[window] = None
            continue
        p = float(price)
        v = float(volume)
        q = float(pressure or 0.0) / 3.25
        if p >= 0:
            score = 0.58 * p + (0.27 * min(v, 3.0) if v >= 0 else 0.48 * v) + 0.15 * q
        else:
            score = 0.62 * p + (0.32 * v if v <= 0 else -0.38 * v) + 0.06 * q
        result[window] = _clamp(score, -5.0, 5.0)
    return result

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
    series = _series_with_current(short_history, now_ms=now_ms, rate=rate, volume=current_volume)
    price_changes, volume_changes = compute_window_changes_from_history(
        current_rate=rate,
        current_volume=current_volume,
        history=series,
        now_ms=now_ms,
    )
    price_baselines, volume_baselines = _window_baselines(series, config)
    window_quality, quality_reasons, quality = _window_data_quality(
        current_volume=current_volume,
        price_changes=price_changes,
        volume_changes=volume_changes,
        volume_baselines=volume_baselines,
        minimum_volume=float(config.get("minimum_reliable_volume_usd", 500_000)),
        maximum_volume_jump_pct=float(config.get("maximum_plausible_volume_jump_pct", 1500.0)),
    )
    price_continuity, volume_continuity, price_jump_share, volume_jump_share = _all_path_stats(series, now_ms)
    price_strengths = {
        window: _robust_signed_strength(
            price_changes.get(window), price_baselines[window], _thresholds(config, "price", window)
        )
        for window in WINDOWS
    }
    volume_strengths = {
        window: _robust_signed_strength(
            volume_changes.get(window), volume_baselines[window], _thresholds(config, "volume", window)
        )
        for window in WINDOWS
    }
    volume_colors = {
        window: _volume_color(
            volume_changes.get(window),
            volume_strengths.get(window),
            volume_baselines[window],
            window_quality[window],
            continuity=volume_continuity[window],
            jump_share=volume_jump_share[window],
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
            price_continuity=price_continuity[window],
            volume_continuity=volume_continuity[window],
            price_jump_share=price_jump_share[window],
            volume_jump_share=volume_jump_share[window],
        )
        accumulation[window] = acc
        distribution[window] = dist
        window_pressures[window] = pressure

    raw_acc_score, raw_dist_score, acc_acceleration, dist_acceleration = _pattern_aggregate(accumulation, distribution)
    current_axis = _clamp((raw_acc_score - raw_dist_score) / 100.0, -1.0, 1.0)
    current_pressure_values = [value for value in window_pressures.values() if value is not None]
    current_pressure = _weighted(window_pressures) / 3.25 if len(current_pressure_values) >= 2 else 0.0
    temporal = _temporal_context(
        series,
        now_ms=now_ms,
        current_axis=current_axis,
        current_pressure=current_pressure,
        config=config,
    )

    # The effective score deliberately retains recent warnings instead of treating every run as a reset.
    positive_temporal = max(temporal.smoothed_axis, 0.0) * 100.0
    negative_temporal = max(-temporal.smoothed_axis, 0.0) * 100.0
    acc_score = 0.55 * raw_acc_score + 0.45 * positive_temporal
    dist_score = 0.55 * raw_dist_score + 0.45 * negative_temporal
    if temporal.reversal_guard:
        if current_axis > 0:
            acc_score *= 0.62
            dist_score = max(dist_score, negative_temporal * 0.85)
        elif current_axis < 0:
            dist_score *= 0.70
            acc_score = max(acc_score, positive_temporal * 0.75)
    acc_score = _clamp(acc_score, 0.0, 100.0)
    dist_score = _clamp(dist_score, 0.0, 100.0)

    good_windows = sum(window_quality.get(window) == "good" for window in WINDOWS)
    strict_acc = _strict_accumulation_extreme(
        accumulation,
        distribution,
        price_changes,
        volume_strengths,
        volume_continuity,
        volume_jump_share,
        config,
        window_quality,
        acc_score,
        temporal,
    )
    strict_dist = _strict_distribution_extreme(
        accumulation,
        distribution,
        price_strengths,
        volume_strengths,
        price_continuity,
        config,
        window_quality,
        dist_score,
        temporal,
    )

    relative_scores: dict[int, float | None] = {}
    if is_reference:
        relative_scores = _reference_confirmation_scores(
            price_strengths, volume_strengths, window_pressures
        )
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
            btc_baseline = (
                btc_short.price_baselines[window]
                if btc_short
                else RobustBaseline(0.0, _thresholds(config, "price", window)[0], 0)
            )
            center = coin_baseline.median - btc_baseline.median
            scale = math.sqrt(coin_baseline.scale**2 + btc_baseline.scale**2)
            relative_scores[window] = _clamp(
                (coin_change - btc_change - center) / max(scale, 1e-9), -5.0, 5.0
            )
        relative_short = _weighted_relative_pct(price_changes, btc_price_changes or {})
        relative_color = _relative_color(relative_scores)

    pressure_values = [value for value in window_pressures.values() if value is not None]
    if len(pressure_values) >= 2:
        current_pressure_score = _weighted(window_pressures)
        temporal_pressure_score = temporal.smoothed_pressure * 3.25
        pressure_score = 0.64 * current_pressure_score + 0.36 * temporal_pressure_score
        if temporal.reversal_guard:
            pressure_score *= 0.78
        pressure_score = _clamp(pressure_score, -3.25, 3.25)
    else:
        pressure_score = None
    p_color = _pressure_color(
        pressure_score,
        window_pressures,
        strict_accumulation=strict_acc,
        strict_distribution=strict_dist,
        quality=quality,
        temporal=temporal,
    )

    buy_count, sell_count = _condition_counts(
        accumulation=accumulation,
        distribution=distribution,
        relative_color=relative_color,
        quality=window_quality,
        price_continuity=price_continuity,
        volume_continuity=volume_continuity,
        volume_jump_share=volume_jump_share,
        temporal=temporal,
    )
    effective_axis = temporal.smoothed_axis
    if abs(effective_axis) < 0.10:
        effective_axis = (acc_score - dist_score) / 100.0
    direction = "▲" if effective_axis > 0.08 else ("▼" if effective_axis < -0.08 else "=")
    signal = _pattern_color(
        accumulation_score=acc_score,
        distribution_score=dist_score,
        strict_accumulation=strict_acc,
        strict_distribution=strict_dist,
        quality=quality,
        good_windows=good_windows,
        temporal=temporal,
    )
    quality_factor = _quality_factor(quality, good_windows)
    persistence_factor = 0.54 + 0.09 * min(max(temporal.positive_streak, temporal.negative_streak), 4)
    persistence_factor *= 0.70 + 0.30 * temporal.consensus
    if temporal.reversal_guard:
        persistence_factor *= 0.72
    proximity = max(acc_score, dist_score) * quality_factor * persistence_factor
    margin = abs(acc_score - dist_score)
    pattern_confidence = _clamp(
        (0.42 * proximity + 0.30 * margin + 28.0 * abs(temporal.smoothed_axis)) / 100.0,
        0.0,
        1.0,
    )
    acceleration = acc_acceleration if effective_axis >= 0 else -dist_acceleration
    anomaly = (
        proximity
        + max(buy_count, sell_count) * 3.5
        + margin * 0.18
        + abs(acceleration) * 0.08
        + abs(temporal.smoothed_axis) * 18.0
    )

    # Flash ranking reacts to a fresh 5–15 minute setup without granting a strong color.
    # Persistence-aware signal colors and X-counts remain the confirmation layer.
    fresh_weights = {10: 0.62, 20: 0.28, 60: 0.10}
    flash_acc = sum((accumulation.get(window) or 0.0) * fresh_weights[window] for window in WINDOWS)
    flash_dist = sum((distribution.get(window) or 0.0) * fresh_weights[window] for window in WINDOWS)
    recent_jump_penalty = 1.0
    if volume_jump_share.get(10, 0.0) > 0.72 or price_jump_share.get(10, 0.0) > 0.72:
        recent_jump_penalty *= 0.72
    if window_quality.get(10) != "good":
        recent_jump_penalty *= 0.72
    flash_raw = max(flash_acc, flash_dist)
    flash_margin = abs(flash_acc - flash_dist)
    flash_score = _clamp(
        (0.72 * flash_raw + 0.20 * flash_margin + 0.08 * abs(acceleration))
        * quality_factor
        * recent_jump_penalty,
        0.0,
        100.0,
    )
    flash_direction = "▲" if flash_acc > flash_dist + 4.0 else ("▼" if flash_dist > flash_acc + 4.0 else "=")

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
        agreement_score=temporal.smoothed_axis * 3.0,
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
        price_continuity=price_continuity,
        volume_continuity=volume_continuity,
        price_jump_share=price_jump_share,
        volume_jump_share=volume_jump_share,
        temporal_axes=temporal.axes,
        temporal_pressures=temporal.pressures,
        temporal_score=temporal.smoothed_axis,
        temporal_pressure=temporal.smoothed_pressure,
        positive_streak=temporal.positive_streak,
        negative_streak=temporal.negative_streak,
        temporal_samples=temporal.samples,
        reversal_guard=temporal.reversal_guard,
        flash_score=flash_score,
        flash_direction=flash_direction,
    )


def _wilson_lower_bound(successes: float, total: float, z: float = 1.2816) -> float:
    """One-sided Wilson lower bound (about 90% confidence)."""
    if total <= 0:
        return 0.0
    p = _clamp(successes / total, 0.0, 1.0)
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return _clamp((centre - margin) / denominator, 0.0, 1.0)


def _completed_daily_observations(
    points: list[PricePoint],
    now: datetime,
    timezone: str,
    lookback_days: int,
) -> list[DailyObservation]:
    """Build exactly one observation per completed local calendar day.

    The current day and the partial first day are excluded. This makes weekday
    output identical throughout the day and avoids counting many correlated
    intraday points as independent samples.
    """
    if len(points) < 3:
        return []
    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz)
    today = local_now.date()
    cutoff_ordinal = today.toordinal()
    earliest_ordinal = cutoff_ordinal - max(lookback_days + 3, 10)

    closes: dict[int, PricePoint] = {}
    for point in points:
        local = datetime.fromtimestamp(point.timestamp_ms / 1000, tz=tz)
        ordinal = local.date().toordinal()
        if ordinal >= cutoff_ordinal or ordinal < earliest_ordinal:
            continue
        previous = closes.get(ordinal)
        if previous is None or point.timestamp_ms > previous.timestamp_ms:
            closes[ordinal] = point
    ordered = sorted(closes.items())
    raw: list[tuple[int, int, int, float, float | None, float]] = []
    for (previous_day, previous), (current_day, current) in zip(ordered, ordered[1:]):
        gap_days = current_day - previous_day
        if gap_days < 1 or gap_days > 2 or previous.rate <= 0:
            continue
        price_pct = _pct(current.rate, previous.rate)
        volume_pct = _pct(current.volume, previous.volume)
        if price_pct is None or not math.isfinite(price_pct):
            continue
        if volume_pct is not None and (not math.isfinite(volume_pct) or abs(volume_pct) > 1500):
            volume_pct = None
        # Consecutive daily closes are most reliable; a two-day gap is retained
        # with reduced weight instead of pretending it is a normal day.
        reliability = 1.0 if gap_days == 1 else 0.62
        timestamp_ms = current.timestamp_ms
        weekday = datetime.fromtimestamp(timestamp_ms / 1000, tz=tz).weekday()
        raw.append((timestamp_ms, current_day, weekday, price_pct, volume_pct, reliability))

    if len(raw) < 14:
        return []
    price_base = _robust_baseline([row[3] for row in raw], 0.25)
    volume_base = _robust_baseline([row[4] for row in raw if row[4] is not None], 0.75)
    scored: list[DailyObservation] = []
    raw_scores: list[float] = []
    staged: list[tuple[tuple[int, int, int, float, float | None, float], float]] = []
    for row in raw:
        timestamp_ms, ordinal, weekday, price_pct, volume_pct, reliability = row
        pz = _clamp(_robust_z(price_pct, price_base) or 0.0, -3.5, 3.5)
        if volume_pct is None:
            score = 0.55 * pz
            reliability *= 0.76
        else:
            vz = _clamp(_robust_z(volume_pct, volume_base) or 0.0, -3.5, 3.5)
            if abs(pz) <= 0.40 and vz > 0:
                score = 0.18 * pz + 0.82 * vz
            elif pz > 0 and vz >= 0:
                score = 0.66 * pz + 0.34 * vz
            elif pz > 0 and vz < 0:
                score = 0.52 * pz + 0.78 * vz
            elif pz < 0 and vz > 0:
                score = 0.74 * pz - 0.48 * vz
            else:
                score = 0.72 * pz + 0.28 * vz
        score = _clamp(score * reliability, -4.0, 4.0)
        staged.append((row, score))
        raw_scores.append(score)

    global_centre = 0.60 * statistics.median(raw_scores) + 0.40 * _trimmed_mean(raw_scores)
    for row, score in staged:
        timestamp_ms, ordinal, weekday, price_pct, volume_pct, reliability = row
        scored.append(
            DailyObservation(
                timestamp_ms=timestamp_ms,
                date_ordinal=ordinal,
                weekday=weekday,
                score=score - global_centre,
                price_pct=price_pct,
                volume_pct=volume_pct,
                reliability=reliability,
            )
        )
    # Keep exactly the requested number of completed local days.
    minimum_ordinal = cutoff_ordinal - lookback_days
    return [item for item in scored if item.date_ordinal >= minimum_ordinal]


def _recency_weight(timestamp_ms: int, now_ms: int, half_life_days: float = 50.0) -> float:
    age_days = max(0.0, (now_ms - timestamp_ms) / 86_400_000)
    return 0.5 ** (age_days / max(half_life_days, 1.0))


def _daily_group_metrics(items: Sequence[DailyObservation], now_ms: int) -> tuple[float, float, float, float, float]:
    if not items:
        return 0.0, 0.0, 9.0, 0.0, 0.0
    weighted = [
        (item.score, _recency_weight(item.timestamp_ms, now_ms, half_life_days=50.0) * item.reliability)
        for item in items
    ]
    total_weight = max(sum(weight for _, weight in weighted), 1e-9)
    median_value = _weighted_median(weighted)
    mean_value = sum(value * weight for value, weight in weighted) / total_weight
    central = 0.72 * median_value + 0.28 * mean_value
    hit_weight = sum(weight for value, weight in weighted if value > 0)
    hit_rate = hit_weight / total_weight
    dispersion = _robust_baseline([item.score for item in items], 0.12).scale
    consistency = _clamp(abs(hit_rate - 0.5) * 2.0, 0.0, 1.0)
    wilson = _wilson_lower_bound(hit_weight, total_weight)
    return central, hit_rate, dispersion, consistency, wilson


def _leave_extremes_out_central(items: Sequence[DailyObservation], now_ms: int) -> float:
    if len(items) < 5:
        return 0.0
    ordered = sorted(items, key=lambda item: item.score)
    trimmed = ordered[1:-1]
    return _daily_group_metrics(trimmed, now_ms)[0]


def analyze_seasonality(
    points: list[PricePoint],
    now: datetime,
    timezone: str,
    block_hours: int = 4,
    min_samples: int = 24,
    minimum_observations: int = 240,
    lookback_days: int = 365,
) -> Seasonality:
    """Conservative weekday statistics from independent completed local days.

    A weekday must be positive across the long sample and recent market regimes.
    Selection hysteresis is applied by the daily cache layer, not here.
    """
    del block_hours
    observations = _completed_daily_observations(points, now, timezone, lookback_days)
    if len(observations) < minimum_observations:
        return Seasonality("?", tuple(), len(observations), "completed-days-insufficient")

    local_now = now.astimezone(ZoneInfo(timezone))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    now_ms = int(local_midnight.timestamp() * 1000)
    local_today = local_now.date().toordinal()
    cutoffs = {
        180: local_today - 180,
        90: local_today - 90,
        45: local_today - 45,
    }
    by_weekday: dict[int, list[DailyObservation]] = {}
    for item in observations:
        by_weekday.setdefault(item.weekday, []).append(item)

    required = max(24, min_samples)
    candidates: list[tuple[int, float, float, int]] = []
    weekday_scores: dict[str, float] = {}
    weekday_confidence: dict[str, float] = {}
    day_summaries: dict[int, tuple[float, float]] = {}

    for weekday in range(7):
        items = by_weekday.get(weekday, [])
        windows = {
            days: [item for item in items if item.date_ordinal >= cutoff]
            for days, cutoff in cutoffs.items()
        }
        if (
            len(items) < required
            or len(windows[180]) < 12
            or len(windows[90]) < 6
            or len(windows[45]) < 3
        ):
            continue

        full = _daily_group_metrics(items, now_ms)
        r180 = _daily_group_metrics(windows[180], now_ms)
        r90 = _daily_group_metrics(windows[90], now_ms)
        r45 = _daily_group_metrics(windows[45], now_ms)
        leave_out = _leave_extremes_out_central(items, now_ms)
        full_central, full_hit, full_dispersion, full_consistency, full_wilson = full
        c180, hit180, _, consistency180, wilson180 = r180
        c90, hit90, _, consistency90, _ = r90
        c45, hit45, _, _, _ = r45

        sample_factor = min(1.0, len(items) / 48.0)
        confidence = sample_factor
        confidence *= (
            0.34
            + 0.20 * full_consistency
            + 0.20 * consistency180
            + 0.16 * consistency90
            + 0.10 * min(1.0, len(windows[45]) / 7.0)
        )
        confidence *= 1.0 / (1.0 + 0.10 * full_dispersion)

        # A recent weak regime may dampen a day, but a single 45-day wobble cannot
        # erase a strong 365/180/90-day result by itself.
        conservative = min(
            full_central,
            c180,
            max(c90, -0.025),
            max(c45, -0.075),
            leave_out,
        )
        hit_support = min(full_hit, hit180, max(hit90, 0.48))
        lower_bound = min(full_wilson, wilson180)
        quality = conservative * confidence * (
            0.52 + 0.28 * hit_support + 0.20 * lower_bound
        )
        name = DAY_NAMES[weekday]
        weekday_scores[name] = round(quality, 5)
        weekday_confidence[name] = round(confidence, 5)
        day_summaries[weekday] = (conservative, confidence)

        strict_qualifies = (
            full_central > 0.070
            and c180 > 0.060
            and c90 > 0.020
            and c45 >= -0.055
            and leave_out > 0.040
            and full_hit >= 0.550
            and hit180 >= 0.540
            and hit90 >= 0.510
            and hit45 >= 0.440
            and full_wilson >= 0.415
            and wilson180 >= 0.380
            and confidence >= 0.555
            and quality > 0.042
        )
        robust_qualifies = (
            full_central > 0.025
            and c180 > 0.020
            and c90 >= -0.015
            and c45 >= -0.100
            and leave_out > 0.015
            and full_hit >= 0.515
            and hit180 >= 0.505
            and hit90 >= 0.470
            and full_wilson >= 0.350
            and wilson180 >= 0.320
            and confidence >= 0.480
            and quality > 0.012
        )
        if strict_qualifies:
            candidates.append((weekday, quality, confidence, 2))
        elif robust_qualifies:
            candidates.append((weekday, quality, confidence, 1))

    candidates.sort(
        key=lambda item: (item[3], round(item[1], 5), round(item[2], 5)),
        reverse=True,
    )
    selected: list[int] = []
    if candidates:
        selected.append(candidates[0][0])
    if len(candidates) >= 2:
        first_quality = candidates[0][1]
        second_quality = candidates[1][1]
        third_quality = candidates[2][1] if len(candidates) >= 3 else 0.0
        second_tier = candidates[1][3]
        clear_from_third = second_quality >= third_quality + 0.010
        independently_strong = (
            second_tier == 2
            or (second_quality >= max(0.030, first_quality * 0.68) and candidates[1][2] >= 0.55)
        )
        if clear_from_third and independently_strong:
            selected.append(candidates[1][0])
    selected.sort(key=DISPLAY_WEEK_ORDER.index)
    best_days = tuple(DAY_NAMES[weekday] for weekday in selected)

    current_weekday = local_now.weekday()
    current_summary = day_summaries.get(current_weekday)
    if current_summary is None:
        current, score, confidence = "?", 0.0, 0.0
    else:
        score, confidence = current_summary
        if confidence < 0.575:
            current = "?"
        elif score >= 0.85:
            current = "++"
        elif score >= 0.14:
            current = "+"
        elif score <= -0.85:
            current = "--"
        elif score <= -0.14:
            current = "-"
        else:
            current = "="
    return Seasonality(
        current=current,
        best_weekdays=best_days,
        samples=len(observations),
        source="completed-calendar-days-365d-tiered",
        current_score=score,
        current_confidence=confidence,
        weekday_scores=weekday_scores,
        weekday_confidence=weekday_confidence,
    )


def rolling_week_returns(points: list[PricePoint]) -> list[float]:
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


def week_context_from_samples(week_pct: float, samples: Sequence[float]) -> tuple[str, float | None, float]:
    samples = [float(value) for value in samples if math.isfinite(float(value))]

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


def _week_context(week_pct: float, points: list[PricePoint]) -> tuple[str, float | None, float]:
    return week_context_from_samples(week_pct, rolling_week_returns(points))


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

    pattern_axis = short.temporal_score
    pressure = (short.pressure_score or 0.0) / 3.25
    relative = color_level(short.relative_color) / 3.0
    week = color_level(week_signal) / 3.0
    historical = 0.0
    if seasonality.current_score is not None and seasonality.current_confidence >= 0.58:
        historical = _clamp(seasonality.current_score / 1.5, -1.0, 1.0) * seasonality.current_confidence

    score = (
        0.62 * pattern_axis
        + 0.20 * pressure
        + 0.10 * relative
        + 0.05 * week
        + 0.03 * historical
    ) * 3.0

    component_signs = [
        1 if pattern_axis >= 0.20 else -1 if pattern_axis <= -0.20 else 0,
        1 if pressure >= 0.20 else -1 if pressure <= -0.20 else 0,
        1 if relative >= 0.34 else -1 if relative <= -0.34 else 0,
    ]
    if 1 in component_signs and -1 in component_signs:
        score *= 0.58

    three_good = all(short.window_quality.get(window) == "good" for window in WINDOWS)
    positive_support = (
        short.signal_color in {GREEN, PURPLE}
        and short.pressure_color in {GREEN, PURPLE}
        and short.relative_color not in {ORANGE, RED}
        and short.positive_streak >= int(config.get("temporal_confirmation_points", 4))
        and short.pattern_confidence >= 0.58
        and not short.reversal_guard
    )
    negative_support = (
        short.signal_color in {ORANGE, RED}
        and short.pressure_color in {ORANGE, RED}
        and short.relative_color not in {BLUE, GREEN, PURPLE}
        and short.negative_streak >= int(config.get("temporal_confirmation_points", 4))
        and short.pattern_confidence >= 0.58
        and not short.reversal_guard
    )
    purple = three_good and short.signal_color == PURPLE and short.pressure_color == PURPLE and positive_support and score >= 1.95
    red = three_good and short.signal_color == RED and short.pressure_color == RED and negative_support and score <= -1.95
    if purple:
        return score, PURPLE
    if red:
        return score, RED
    if short.reversal_guard:
        if score >= 0.70:
            return score, BLUE
        if score <= -0.55:
            return score, ORANGE
        return score, YELLOW
    if score >= 1.20 and positive_support:
        return score, GREEN
    if score >= 0.45 and short.temporal_score >= 0.16 and short.positive_streak >= 2:
        return score, BLUE
    if score <= -1.15 and negative_support:
        return score, ORANGE
    if score <= -0.45 and short.temporal_score <= -0.16 and short.negative_streak >= 2:
        return score, ORANGE
    return score, YELLOW

def btc_gate(short: ShortMetrics, config: Mapping[str, Any]) -> bool:
    return (
        short.signal_color in {GREEN, PURPLE}
        and short.pressure_color in {GREEN, PURPLE}
        and short.relative_color in {BLUE, GREEN, PURPLE}
        and short.positive_streak >= int(config.get("temporal_confirmation_points", 4))
        and short.pattern_confidence >= 0.58
        and not short.reversal_guard
        and short.data_quality == "good"
    )

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
    seasonality_override: Seasonality | None = None,
    week_samples_override: Sequence[float] | None = None,
    map_flash_score: float = 0.0,
) -> CoinAnalysis:
    week_pct = delta_to_pct((current.get("delta") or {}).get("week"))
    seasonality = seasonality_override or analyze_seasonality(
        history,
        now,
        timezone,
        block_hours=block_hours,
        min_samples=min_samples,
        minimum_observations=minimum_observations,
        lookback_days=int(config.get("seasonality_lookback_days", 365)),
    )
    if week_samples_override is not None:
        week_signal, percentile, week_confidence = week_context_from_samples(
            week_pct, week_samples_override
        )
    else:
        week_signal, percentile, week_confidence = _week_context(week_pct, history)
    now_score, now_color = current_now_signal(
        short,
        seasonality,
        config,
        is_reference=is_reference,
        week_signal=week_signal,
    )

    count = max(short.buy_count, short.sell_count)
    persistence = max(short.positive_streak, short.negative_streak)
    confirmed = (
        count * 11.0
        + short.extreme_proximity * 0.62
        + short.pattern_confidence * 24.0
        + persistence * 3.5
        + (4.0 if not short.reversal_guard else -7.0)
    )
    # Map flash keeps every configured coin eligible; detailed flash reacts to
    # the newest 10/20-minute shape. Neither changes the conservative colors.
    flash = max(short.flash_score, min(100.0, map_flash_score))
    ranking_score = 0.74 * confirmed + 0.52 * flash
    if short.data_quality == "uncertain":
        ranking_score *= 0.86
    elif short.data_quality == "insufficient":
        ranking_score *= 0.55

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
        flash_score=flash,
        ranking_score=ranking_score,
    )


def strength_count(item: CoinAnalysis) -> int:
    return max(item.short.buy_count, item.short.sell_count)


def confidence_sort_key(item: CoinAnalysis) -> tuple[float, ...]:
    quality_rank = {"good": 2.0, "uncertain": 1.0, "insufficient": 0.0}.get(item.short.data_quality, 0.0)
    count = strength_count(item)
    persistence = max(item.short.positive_streak, item.short.negative_streak)
    # Ranking score includes the fast flash layer; every visible color/count
    # remains persistence-confirmed and therefore deliberately slower.
    return (
        float(math.floor(item.ranking_score / 3.0)),
        float(count),
        float(math.floor(item.flash_score / 5.0)),
        float(math.floor(item.short.extreme_proximity / 5.0)),
        float(persistence),
        0.0 if item.short.reversal_guard else 1.0,
        quality_rank,
        float(math.floor(item.short.pattern_confidence * 10.0)),
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
