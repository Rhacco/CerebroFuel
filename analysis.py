"""Deterministic short-term crypto anomaly analysis for Discord."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

DAY_NAMES = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
MIN_TIME_OBSERVATIONS = 40
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
            if rate <= 0:
                continue
            raw_volume = row.get("volume")
            volume = float(raw_volume) if raw_volume not in (None, "") else None
            if volume is not None and volume < 0:
                volume = None
        except (KeyError, TypeError, ValueError):
            continue
        by_timestamp[timestamp] = PricePoint(timestamp, rate, volume)
    return sorted(by_timestamp.values(), key=lambda point: point.timestamp_ms)


def _median(values: Iterable[float]) -> float | None:
    cleaned: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            cleaned.append(number)
    return statistics.median(cleaned) if cleaned else None


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


def _find_snapshot_value(
    snapshots: list[dict[str, Any]],
    api_code: str,
    target_ms: int,
    tolerance_ms: int,
) -> tuple[float | None, float | None]:
    best: tuple[int, float | None, float | None] | None = None
    for snapshot in snapshots:
        try:
            ts = int(snapshot["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = abs(ts - target_ms)
        if distance > tolerance_ms:
            continue
        coins = snapshot.get("coins")
        if not isinstance(coins, dict):
            continue
        row = coins.get(api_code)
        if not isinstance(row, dict):
            continue
        try:
            rate = float(row["rate"])
            volume_raw = row.get("volume")
            volume = float(volume_raw) if volume_raw not in (None, "") else None
        except (KeyError, TypeError, ValueError):
            continue
        if rate <= 0:
            continue
        if best is None or distance < best[0]:
            best = (distance, rate, volume)
    if best is None:
        return None, None
    return best[1], best[2]


def _pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def compute_window_changes(
    *,
    api_code: str,
    current_rate: float,
    current_volume: float | None,
    snapshots: list[dict[str, Any]],
    now_ms: int,
    tolerance_minutes: int,
) -> tuple[dict[int, float | None], dict[int, float | None]]:
    price_changes: dict[int, float | None] = {}
    volume_changes: dict[int, float | None] = {}
    tolerance_ms = tolerance_minutes * 60_000
    for window in WINDOWS:
        previous_rate, previous_volume = _find_snapshot_value(
            snapshots,
            api_code,
            now_ms - window * 60_000,
            tolerance_ms,
        )
        price_changes[window] = _pct(current_rate, previous_rate)
        volume_changes[window] = _pct(current_volume, previous_volume)
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
    price_thresholds: Mapping[str, Any],
) -> float | None:
    weights = {10: 0.45, 20: 0.35, 60: 0.20}
    values: list[tuple[float, float]] = []
    for window, weight in weights.items():
        change = price_changes.get(window)
        if change is None:
            continue
        light, clear, strong = _thresholds(price_thresholds, "price", window)
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
    price_changes: Mapping[int, float | None],
    volume_changes: Mapping[int, float | None],
    minimum_volume: float,
    maximum_volume_jump_pct: float,
) -> str:
    usable_price = sum(value is not None for value in price_changes.values())
    usable_volume = sum(value is not None for value in volume_changes.values())
    if usable_price < 2 or usable_volume < 2:
        return "insufficient"
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
        light, clear, _ = _thresholds(config, "price", window)
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
        score *= 0.72
    elif quality == "insufficient":
        score *= 0.58
    return score


def build_short_metrics(
    *,
    api_code: str,
    current: Mapping[str, Any],
    snapshots: list[dict[str, Any]],
    now_ms: int,
    btc_price_changes: Mapping[int, float | None] | None,
    config: Mapping[str, Any],
    is_reference: bool,
) -> ShortMetrics:
    rate = float(current["rate"])
    raw_volume = current.get("volume")
    current_volume = float(raw_volume) if raw_volume not in (None, "") else None
    price_changes, volume_changes = compute_window_changes(
        api_code=api_code,
        current_rate=rate,
        current_volume=current_volume,
        snapshots=snapshots,
        now_ms=now_ms,
        tolerance_minutes=int(config.get("snapshot_tolerance_minutes", 7)),
    )
    quality = _data_quality(
        current_volume=current_volume,
        price_changes=price_changes,
        volume_changes=volume_changes,
        minimum_volume=float(config.get("minimum_reliable_volume_usd", 500_000)),
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
            uncertain=False,
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


def _return_observations(
    points: list[PricePoint], timezone: str, block_hours: int
) -> list[tuple[int, int, float]]:
    if len(points) < 2:
        return []
    tz = ZoneInfo(timezone)
    observations: list[tuple[int, int, float]] = []
    for previous, current in zip(points, points[1:]):
        elapsed_hours = (current.timestamp_ms - previous.timestamp_ms) / 3_600_000
        if elapsed_hours < 0.4 or elapsed_hours > 4.0 or previous.rate <= 0:
            continue
        raw_return = (current.rate / previous.rate - 1.0) * 100.0
        hourly_return = raw_return / elapsed_hours
        local_dt = datetime.fromtimestamp(current.timestamp_ms / 1000, tz=tz)
        block = (local_dt.hour // block_hours) * block_hours
        observations.append((local_dt.weekday(), block, hourly_return))
    return observations


def analyze_seasonality(
    points: list[PricePoint],
    now: datetime,
    timezone: str,
    block_hours: int = 4,
    min_samples: int = 4,
) -> Seasonality:
    observations = _return_observations(points, timezone, block_hours)
    if len(observations) < MIN_TIME_OBSERVATIONS:
        return Seasonality("?", tuple(), len(observations))
    by_slot: dict[tuple[int, int], list[float]] = {}
    by_weekday: dict[int, list[float]] = {}
    all_returns: list[float] = []
    for weekday, block, value in observations:
        by_slot.setdefault((weekday, block), []).append(value)
        by_weekday.setdefault(weekday, []).append(value)
        all_returns.append(value)

    def quality(values: list[float]) -> float:
        avg = statistics.mean(values)
        hit_rate = sum(value > 0 for value in values) / len(values)
        return avg * (0.45 + hit_rate)

    eligible = {
        weekday: values
        for weekday, values in by_weekday.items()
        if len(values) >= max(10, min_samples)
    }
    ranked = sorted(eligible, key=lambda weekday: quality(eligible[weekday]), reverse=True)
    best_days: list[str] = []
    if ranked:
        scores = [quality(eligible[weekday]) for weekday in ranked]
        best_score = scores[0]
        take = min(2, len(ranked))
        if len(ranked) >= 3 and scores[2] > 0 and scores[2] >= best_score * 0.60:
            take = 3
        if len(ranked) >= 4 and scores[3] > 0 and scores[3] >= best_score * 0.42:
            take = 4
        best_days = [DAY_NAMES[weekday] for weekday in ranked[:take]]

    local_now = now.astimezone(ZoneInfo(timezone))
    current_block = (local_now.hour // block_hours) * block_hours
    current_values = by_slot.get((local_now.weekday(), current_block), [])
    median_abs = _median(abs(value) for value in all_returns) or 0.0
    threshold = max(0.02, median_abs * 0.18)
    if len(current_values) < min_samples:
        current = "?"
    else:
        avg = statistics.mean(current_values)
        hit = sum(value > 0 for value in current_values) / len(current_values)
        if avg > threshold and hit >= 0.56:
            current = "+"
        elif avg < -2.0 * threshold and hit <= 0.38:
            current = "⚠"
        elif avg < -threshold and hit <= 0.44:
            current = "-"
        else:
            current = "="
    return Seasonality(current, tuple(best_days), len(observations))


def time_color(mark: str) -> str:
    return {
        "+": GREEN,
        "=": YELLOW,
        "-": ORANGE,
        "⚠": RED,
        "?": WHITE,
    }.get(mark, WHITE)


def week_color(week_pct: float) -> str:
    return signed_color(week_pct, light=0.75, clear=3.0, strong=10.0)


def btc_gate(short: ShortMetrics, config: Mapping[str, Any]) -> bool:
    raw = config.get("btc_no_drop_pct", {})
    defaults = {10: -0.10, 20: -0.15, 60: -0.25}
    for window in WINDOWS:
        value = short.price_changes.get(window)
        limit = float(raw.get(str(window), defaults[window])) if isinstance(raw, Mapping) else defaults[window]
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


def format_line(
    item: CoinAnalysis,
    *,
    generated_at: datetime,
    timezone: str,
) -> str:
    denominator = 7 if item.is_reference else 8
    weekdays = "/".join(item.seasonality.best_weekdays) or "?"
    volumes = "".join(item.short.volume_colors.get(window, WHITE) for window in WINDOWS)
    count = strength_count(item)
    if item.is_reference:
        time_text = generated_at.astimezone(ZoneInfo(timezone)).strftime("%H:%M")
        gate = GREEN if item.btc_gate else BLACK
        return (
            f"₿{time_text} {gate} · {count}/{denominator}{item.short.direction} · "
            f"7d{item.week_color} · P{item.short.pressure_color} · "
            f"V{volumes} · N{time_color(item.seasonality.current)} · {weekdays}"
        )
    return (
        f"{item.short.signal_color} {item.display_code} · {count}/{denominator}{item.short.direction} · "
        f"7d{item.week_color} · vB{item.short.relative_color} · "
        f"P{item.short.pressure_color} · V{volumes} · "
        f"N{time_color(item.seasonality.current)} · {weekdays}"
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
