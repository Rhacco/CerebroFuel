"""Deterministic short-term crypto anomaly analysis for Discord (v3.2.1)."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

DAY_NAMES = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
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


@dataclass
class CoinAnalysis:
    display_code: str
    api_code: str
    price: float
    week_pct: float
    week_color: str
    short: ShortMetrics
    seasonality: Seasonality
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


def abbreviate_code(code: str) -> str:
    """Keep codes <=3 chars; shorten longer codes deterministically to 3 chars."""
    cleaned = "".join(char for char in code.upper() if char.isalnum())
    if len(cleaned) <= 3:
        return cleaned
    vowels = set("AEIOU")
    result = cleaned[0]
    for char in cleaned[1:]:
        if char not in vowels and char not in result:
            result += char
        if len(result) == 3:
            return result
    for char in reversed(cleaned[1:]):
        if len(result) == 3:
            break
        if char not in result:
            result += char
    return result[:3]


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


def _pressure(
    price_changes: Mapping[int, float | None],
    volume_colors: Mapping[int, str],
    config: Mapping[str, Any],
) -> float | None:
    weights = {10: 0.45, 20: 0.35, 60: 0.20}
    values: list[tuple[float, float]] = []
    for window, weight in weights.items():
        change = price_changes.get(window)
        if change is None:
            continue
        light, clear, strong = _thresholds(config, "price", window)
        absolute = abs(change)
        if absolute >= strong:
            price_level = 3.0
        elif absolute >= clear:
            price_level = 2.0
        elif absolute >= light:
            price_level = 1.0
        else:
            price_level = 0.25
        if change < 0:
            price_level *= -1
        volume_level = color_level(volume_colors.get(window, WHITE))
        if volume_level > 0:
            multiplier = 1.0 + 0.22 * volume_level
        elif volume_level < 0:
            multiplier = 0.72
        else:
            multiplier = 0.90
        values.append((price_level * multiplier, weight))
    if len(values) < 2:
        return None
    total_weight = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / total_weight


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


def _data_quality(
    *,
    current_volume: float | None,
    short_history_points: int,
    price_changes: Mapping[int, float | None],
    volume_changes: Mapping[int, float | None],
    minimum_volume: float,
    minimum_short_points: int,
    maximum_volume_jump_pct: float,
) -> str:
    usable_price = sum(value is not None for value in price_changes.values())
    usable_volume = sum(value is not None for value in volume_changes.values())
    if usable_price < 2 or usable_volume < 2:
        return "insufficient"
    if short_history_points < minimum_short_points:
        return "uncertain"
    if current_volume is None or current_volume <= 0 or current_volume < minimum_volume:
        return "uncertain"
    if any(
        value is not None and abs(value) > maximum_volume_jump_pct
        for value in volume_changes.values()
    ):
        return "uncertain"
    return "good"


def _count_conditions(
    *,
    price_changes: Mapping[int, float | None],
    volume_colors: Mapping[int, str],
    relative_color: str,
    p_color: str,
    config: Mapping[str, Any],
    is_reference: bool,
) -> tuple[int, int]:
    buy: list[bool] = []
    sell: list[bool] = []
    for window in WINDOWS:
        value = price_changes.get(window)
        _, clear, _ = _thresholds(config, "price", window)
        buy.append(value is not None and value >= clear)
        sell.append(value is not None and value <= -clear)
    for window in WINDOWS:
        value = price_changes.get(window)
        light, _, _ = _thresholds(config, "price", window)
        rising_volume = volume_colors.get(window) in {GREEN, PURPLE}
        buy.append(value is not None and value >= light and rising_volume)
        sell.append(value is not None and value <= -light and rising_volume)
    if not is_reference:
        buy.append(relative_color in {GREEN, PURPLE})
        sell.append(relative_color in {ORANGE, RED})
    buy.append(p_color in {GREEN, PURPLE})
    sell.append(p_color in {ORANGE, RED})
    return sum(buy), sum(sell)


def _anomaly_score(
    *,
    price_changes: Mapping[int, float | None],
    volume_colors: Mapping[int, str],
    relative_color: str,
    p_score: float | None,
    week_pct: float,
    quality: str,
    config: Mapping[str, Any],
    fallback_hour_pct: float,
    fallback_day_pct: float,
) -> float:
    score = 0.0
    price_weights = {10: 3.2, 20: 2.6, 60: 2.0}
    volume_weights = {10: 2.0, 20: 1.7, 60: 1.3}
    usable = 0
    for window in WINDOWS:
        value = price_changes.get(window)
        if value is not None:
            light, clear, strong = _thresholds(config, "price", window)
            normalized = min(abs(value) / max(light, 1e-9), 6.0)
            if abs(value) >= strong:
                normalized += 1.5
            elif abs(value) >= clear:
                normalized += 0.7
            score += normalized * price_weights[window]
            usable += 1
        score += abs(color_level(volume_colors.get(window, WHITE))) * volume_weights[window]
    score += abs(color_level(relative_color)) * 2.2
    if p_score is not None:
        score += min(abs(p_score), 4.0) * 3.0
    score += min(abs(week_pct) / 3.0, 3.0) * 0.8
    if usable < 2:
        score += min(abs(fallback_hour_pct) * 8.0 + abs(fallback_day_pct) * 1.8, 15.0)
    if quality == "uncertain":
        score *= 0.82
    elif quality == "insufficient":
        score *= 0.62
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
    quality = _data_quality(
        current_volume=current_volume,
        short_history_points=len(short_history),
        price_changes=price_changes,
        volume_changes=volume_changes,
        minimum_volume=float(config.get("minimum_reliable_volume_usd", 500_000)),
        minimum_short_points=int(config.get("minimum_short_history_points", 4)),
        maximum_volume_jump_pct=float(config.get("maximum_plausible_volume_jump_pct", 500.0)),
    )
    uncertain = quality == "uncertain"
    volume_colors = {
        window: signed_color(
            volume_changes[window],
            light=_thresholds(config, "volume", window)[0],
            clear=_thresholds(config, "volume", window)[1],
            strong=_thresholds(config, "volume", window)[2],
            uncertain=uncertain,
        )
        for window in WINDOWS
    }
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
    p_score = _pressure(price_changes, volume_colors, config)
    p_color = pressure_color(p_score, uncertain=uncertain)
    buy_count, sell_count = _count_conditions(
        price_changes=price_changes,
        volume_colors=volume_colors,
        relative_color=relative_color,
        p_color=p_color,
        config=config,
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
        price_changes=price_changes,
        volume_colors=volume_colors,
        relative_color=relative_color,
        p_score=p_score,
        week_pct=week_pct,
        quality=quality,
        config=config,
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
    )


def _median(values: Iterable[float]) -> float | None:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.median(cleaned) if cleaned else None


def _return_observations(
    points: list[PricePoint], timezone: str, block_hours: int
) -> tuple[list[tuple[int, int | None, float]], float | None]:
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
    observations: list[tuple[int, int | None, float]] = []
    for previous, current in zip(points, points[1:]):
        elapsed_hours = (current.timestamp_ms - previous.timestamp_ms) / 3_600_000
        if elapsed_hours < lower or elapsed_hours > upper or previous.rate <= 0:
            continue
        raw_return = (current.rate / previous.rate - 1.0) * 100.0
        adjusted_return = raw_return / max(math.sqrt(elapsed_hours), 1.0)
        local_dt = datetime.fromtimestamp(current.timestamp_ms / 1000, tz=tz)
        block = (local_dt.hour // block_hours) * block_hours if median_interval <= 8.0 else None
        observations.append((local_dt.weekday(), block, adjusted_return))
    return observations, median_interval


def _classify_time(values: list[float], all_values: list[float], min_samples: int) -> str:
    if len(values) < min_samples:
        return "="
    median_abs = _median(abs(value) for value in all_values) or 0.0
    threshold = max(0.015, median_abs * 0.16)
    avg = statistics.mean(values)
    hit = sum(value > 0 for value in values) / len(values)
    if avg > threshold and hit >= 0.56:
        return "+"
    if avg < -2.0 * threshold and hit <= 0.38:
        return "⚠"
    if avg < -threshold and hit <= 0.44:
        return "-"
    return "="


def analyze_seasonality(
    points: list[PricePoint],
    now: datetime,
    timezone: str,
    block_hours: int = 4,
    min_samples: int = 4,
    minimum_observations: int = 20,
) -> Seasonality:
    observations, _ = _return_observations(points, timezone, block_hours)
    if len(observations) < minimum_observations:
        return Seasonality("=", tuple(), len(observations), "neutral-fallback")
    by_slot: dict[tuple[int, int], list[float]] = {}
    by_weekday: dict[int, list[float]] = {}
    all_returns: list[float] = []
    for weekday, block, value in observations:
        if block is not None:
            by_slot.setdefault((weekday, block), []).append(value)
        by_weekday.setdefault(weekday, []).append(value)
        all_returns.append(value)

    def quality(values: list[float]) -> float:
        avg = statistics.mean(values)
        hit_rate = sum(value > 0 for value in values) / len(values)
        return avg * (0.45 + hit_rate)

    weekday_min = max(3, min_samples)
    eligible = {
        weekday: values
        for weekday, values in by_weekday.items()
        if len(values) >= weekday_min
    }
    ranked = sorted(eligible, key=lambda weekday: quality(eligible[weekday]), reverse=True)
    best_days: list[str] = []
    if ranked:
        scores = [quality(eligible[weekday]) for weekday in ranked]
        best_score = scores[0]
        take = 1
        if len(ranked) >= 2:
            second_score = scores[1]
            # Nur einen zweiten Tag anzeigen, wenn er dem besten Tag klar nahekommt.
            if best_score > 0 and second_score >= best_score * 0.65:
                take = 2
        best_days = [DAY_NAMES[weekday] for weekday in ranked[:take]]

    local_now = now.astimezone(ZoneInfo(timezone))
    current_block = (local_now.hour // block_hours) * block_hours
    current_slot = by_slot.get((local_now.weekday(), current_block), [])
    if len(current_slot) >= min_samples:
        current = _classify_time(current_slot, all_returns, min_samples)
        source = "weekday-block"
    else:
        current_day = by_weekday.get(local_now.weekday(), [])
        current = _classify_time(current_day, all_returns, weekday_min)
        source = "weekday" if len(current_day) >= weekday_min else "neutral-fallback"
    return Seasonality(current, tuple(best_days), len(observations), source)


def time_color(mark: str) -> str:
    return {
        "+": GREEN,
        "=": YELLOW,
        "-": ORANGE,
        "⚠": RED,
    }.get(mark, YELLOW)


def week_color(week_pct: float) -> str:
    return signed_color(week_pct, light=0.75, clear=3.0, strong=10.0)


def btc_gate(short: ShortMetrics, config: Mapping[str, Any]) -> bool:
    raw = config.get("btc_no_drop_pct", {})
    defaults = {10: -0.10, 20: -0.15, 60: -0.25}
    for window in WINDOWS:
        value = short.price_changes.get(window)
        limit = (
            float(raw.get(str(window), defaults[window]))
            if isinstance(raw, Mapping)
            else defaults[window]
        )
        if value is None or value < limit:
            return False
        if short.volume_colors.get(window) not in {GREEN, PURPLE}:
            return False
    return True


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
    return CoinAnalysis(
        display_code=display_code,
        api_code=api_code,
        price=float(current["rate"]),
        week_pct=week_pct,
        week_color=week_color(week_pct),
        short=short,
        seasonality=seasonality,
        is_reference=is_reference,
        btc_gate=btc_gate(short, config) if is_reference else False,
    )


def strength_count(item: CoinAnalysis) -> int:
    return max(item.short.buy_count, item.short.sell_count)


def format_line(item: CoinAnalysis, *, generated_at: datetime, timezone: str) -> str:
    denominator = 7 if item.is_reference else 8
    volumes = "".join(item.short.volume_colors.get(window, WHITE) for window in WINDOWS)
    count = strength_count(item)
    # Maximal zwei stärkste Tage, ohne Trennzeichen: z. B. DIDO oder FR.
    weekday_suffix = "".join(item.seasonality.best_weekdays[:2])
    if item.is_reference:
        minute_text = generated_at.astimezone(ZoneInfo(timezone)).strftime(":%M")
        market_gate = GREEN if item.btc_gate else BLACK
        return (
            f"{market_gate}{minute_text} {count}/{denominator}{item.short.direction}"
            f"7{item.week_color}B{market_gate}P{item.short.pressure_color}"
            f"V{volumes}N{time_color(item.seasonality.current)}{weekday_suffix}"
        )
    code = abbreviate_code(item.display_code)
    return (
        f"{item.short.signal_color}{code}{count}/{denominator}{item.short.direction}"
        f"7{item.week_color}B{item.short.relative_color}"
        f"P{item.short.pressure_color}V{volumes}"
        f"N{time_color(item.seasonality.current)}{weekday_suffix}"
    )


def build_report(
    reference: CoinAnalysis,
    top_coins: list[CoinAnalysis],
    *,
    generated_at: datetime,
    timezone: str,
) -> str:
    lines = [format_line(reference, generated_at=generated_at, timezone=timezone)]
    lines.extend(format_line(item, generated_at=generated_at, timezone=timezone) for item in top_coins)
    return "\n".join(lines)


def analysis_to_dict(item: CoinAnalysis) -> dict[str, Any]:
    return asdict(item)
