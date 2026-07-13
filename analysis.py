"""Deterministic market analysis and compact Discord formatting."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

DAY_NAMES = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
MIN_TIME_OBSERVATIONS = 40


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
class CoinAnalysis:
    display_code: str
    api_code: str
    price: float
    hour_pct: float
    day_pct: float
    week_pct: float
    relative_day_pct: float
    relative_week_pct: float
    volume_24h_pct: float | None
    volume_7d_pct: float | None
    day_mark: str
    week_mark: str
    relative_mark: str
    pressure: str
    seasonality: Seasonality
    buy_count: int
    sell_count: int
    direction: str
    recommendation: str
    eligible: bool
    buy_flags: dict[str, bool]
    sell_flags: dict[str, bool]


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
            volume_raw = row.get("volume")
            volume = float(volume_raw) if volume_raw not in (None, "") else None
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


def _volumes_between(
    points: list[PricePoint], start_ms: int, end_ms: int
) -> list[float]:
    return [
        float(point.volume)
        for point in points
        if start_ms <= point.timestamp_ms <= end_ms
        and point.volume is not None
        and point.volume > 0
    ]


def compute_volume_trends(
    current_volume: float | None,
    points: list[PricePoint],
    now_ms: int,
) -> tuple[float | None, float | None]:
    """Compare LCW rolling 24h volume with earlier rolling-volume snapshots.

    24h trend: recent six-hour median versus the 18-30h-old median.
    7d trend: recent 24h median versus the 6-7-day-old median.
    """
    if current_volume is None or current_volume <= 0:
        return None, None

    hour = 60 * 60 * 1000
    recent_6h = _volumes_between(points, now_ms - 6 * hour, now_ms)
    recent_24h = _volumes_between(points, now_ms - 24 * hour, now_ms)
    recent_6h.append(current_volume)
    recent_24h.append(current_volume)

    baseline_24h = _volumes_between(points, now_ms - 30 * hour, now_ms - 18 * hour)
    baseline_7d = _volumes_between(points, now_ms - 7 * 24 * hour, now_ms - 6 * 24 * hour)

    def pct(recent: list[float], baseline: list[float]) -> float | None:
        recent_med = _median(recent)
        baseline_med = _median(baseline)
        if recent_med is None or baseline_med is None or baseline_med <= 0:
            return None
        return (recent_med / baseline_med - 1.0) * 100.0

    return pct(recent_6h, baseline_24h), pct(recent_24h, baseline_7d)


def marks_from_value(
    value: float | None,
    *,
    light: float,
    clear: float,
    strong: float,
) -> str:
    if value is None or not math.isfinite(value):
        return "?"
    if value >= strong:
        return "+++"
    if value >= clear:
        return "++"
    if value >= light:
        return "+"
    if value <= -strong:
        return "---"
    if value <= -clear:
        return "--"
    if value <= -light:
        return "-"
    return "="


def mark_level(mark: str) -> int:
    return {
        "+++": 3,
        "++": 2,
        "+": 1,
        "=": 0,
        "?": 0,
        "-": -1,
        "--": -2,
        "---": -3,
    }.get(mark, 0)


def combined_relative_mark(relative_day_pct: float, relative_week_pct: float) -> str:
    day = mark_level(
        marks_from_value(relative_day_pct, light=0.5, clear=2.0, strong=5.0)
    )
    week = mark_level(
        marks_from_value(relative_week_pct, light=1.5, clear=5.0, strong=10.0)
    )
    combined = 0.45 * day + 0.55 * week
    if combined >= 2.35:
        return "+++"
    if combined >= 1.35:
        return "++"
    if combined >= 0.45:
        return "+"
    if combined <= -2.35:
        return "---"
    if combined <= -1.35:
        return "--"
    if combined <= -0.45:
        return "-"
    return "="


def classify_pressure(
    day_pct: float,
    hour_pct: float,
    volume_24h_pct: float | None,
    volume_7d_pct: float | None,
) -> str:
    """Estimate directional pressure from price direction and volume confirmation."""
    vol24 = volume_24h_pct if volume_24h_pct is not None else 0.0
    vol7 = volume_7d_pct if volume_7d_pct is not None else 0.0
    direction = day_pct + 0.35 * hour_pct

    if direction >= 5.0 and vol24 >= 45 and vol7 >= 20:
        return "+++"
    if direction >= 2.0 and vol24 >= 25:
        return "++"
    if direction >= 0.5 and vol24 >= 12:
        return "+"
    if direction <= -5.0 and vol24 >= 45 and vol7 >= 20:
        return "---"
    if direction <= -2.0 and vol24 >= 25:
        return "--"
    if direction <= -0.5 and vol24 >= 12:
        return "-"
    return "="


def _return_observations(
    points: list[PricePoint], timezone: str, block_hours: int
) -> list[tuple[int, int, float]]:
    if len(points) < 2:
        return []
    tz = ZoneInfo(timezone)
    observations: list[tuple[int, int, float]] = []
    for previous, current in zip(points, points[1:]):
        elapsed_hours = (current.timestamp_ms - previous.timestamp_ms) / 3_600_000
        if elapsed_hours < 0.5 or elapsed_hours > 3.5 or previous.rate <= 0:
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

    eligible_weekdays = {
        weekday: values
        for weekday, values in by_weekday.items()
        if len(values) >= max(10, min_samples)
    }
    ranked = sorted(
        eligible_weekdays,
        key=lambda weekday: quality(eligible_weekdays[weekday]),
        reverse=True,
    )

    best_days: list[str] = []
    if ranked:
        scores = [quality(eligible_weekdays[weekday]) for weekday in ranked]
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
        current_label = "?"
    else:
        current_avg = statistics.mean(current_values)
        current_hit = sum(value > 0 for value in current_values) / len(current_values)
        if current_avg > threshold and current_hit >= 0.56:
            current_label = "+"
        elif current_avg < -2.0 * threshold and current_hit <= 0.38:
            current_label = "⚠"
        elif current_avg < -threshold and current_hit <= 0.44:
            current_label = "-"
        else:
            current_label = "="

    return Seasonality(current_label, tuple(best_days), len(observations))


def _direction_from_counts(buy_count: int, sell_count: int) -> tuple[str, str, int]:
    if buy_count > sell_count:
        return "▲", "BUY", buy_count
    if sell_count > buy_count:
        return "▼", "SELL", sell_count
    return "=", "NEUTRAL", buy_count


def analyze_coin(
    *,
    display_code: str,
    api_code: str,
    current: dict[str, Any],
    history: list[PricePoint],
    btc_day_pct: float,
    btc_week_pct: float,
    now: datetime,
    timezone: str,
    block_hours: int,
    min_samples: int,
    recommendation_threshold: int,
    is_reference: bool = False,
) -> CoinAnalysis:
    delta = current.get("delta") or {}
    price = float(current["rate"])
    hour_pct = delta_to_pct(delta.get("hour"))
    day_pct = delta_to_pct(delta.get("day"))
    week_pct = delta_to_pct(delta.get("week"))
    relative_day = 0.0 if is_reference else day_pct - btc_day_pct
    relative_week = 0.0 if is_reference else week_pct - btc_week_pct
    now_ms = int(now.timestamp() * 1000)

    volume_raw = current.get("volume")
    current_volume = float(volume_raw) if volume_raw not in (None, "") else None
    volume_24h_pct, volume_7d_pct = compute_volume_trends(
        current_volume, history, now_ms
    )

    day_mark = marks_from_value(day_pct, light=0.5, clear=2.0, strong=5.0)
    week_mark = marks_from_value(week_pct, light=1.5, clear=5.0, strong=10.0)
    relative_mark = (
        "=" if is_reference else combined_relative_mark(relative_day, relative_week)
    )
    pressure = classify_pressure(day_pct, hour_pct, volume_24h_pct, volume_7d_pct)
    seasonality = analyze_seasonality(
        history,
        now,
        timezone,
        block_hours=block_hours,
        min_samples=min_samples,
    )

    buy_flags = {
        "price_24h": day_pct >= 0.5,
        "price_7d": week_pct >= 1.5,
        "volume_24h": volume_24h_pct is not None and volume_24h_pct >= 15.0,
        "volume_7d": volume_7d_pct is not None and volume_7d_pct >= 20.0,
        "vs_btc_24h": (not is_reference) and relative_day >= 0.5,
        "vs_btc_7d": (not is_reference) and relative_week >= 1.5,
        "pressure": pressure in {"+", "++", "+++"},
        "current_time": seasonality.current == "+",
    }
    sell_flags = {
        "price_24h": day_pct <= -0.5,
        "price_7d": week_pct <= -1.5,
        "volume_24h": (
            volume_24h_pct is not None and volume_24h_pct >= 15.0 and day_pct < 0
        ),
        "volume_7d": (
            volume_7d_pct is not None and volume_7d_pct >= 20.0 and week_pct < 0
        ),
        "vs_btc_24h": (not is_reference) and relative_day <= -0.5,
        "vs_btc_7d": (not is_reference) and relative_week <= -1.5,
        "pressure": pressure in {"-", "--", "---"},
        "current_time": seasonality.current in {"-", "⚠"},
    }

    buy_count = sum(buy_flags.values())
    sell_count = sum(sell_flags.values())
    direction, recommendation, _ = _direction_from_counts(buy_count, sell_count)

    if is_reference:
        eligible = True
    elif recommendation == "BUY":
        eligible = (
            buy_count >= recommendation_threshold
            and buy_flags["volume_24h"]
            and (buy_flags["volume_7d"] or buy_flags["vs_btc_24h"] or buy_flags["vs_btc_7d"])
            and buy_flags["pressure"]
            and seasonality.current not in {"-", "⚠"}
        )
    elif recommendation == "SELL":
        eligible = (
            sell_count >= recommendation_threshold
            and sell_flags["volume_24h"]
            and sell_flags["pressure"]
            and (sell_flags["vs_btc_24h"] or sell_flags["vs_btc_7d"])
        )
    else:
        eligible = False

    return CoinAnalysis(
        display_code=display_code,
        api_code=api_code,
        price=price,
        hour_pct=hour_pct,
        day_pct=day_pct,
        week_pct=week_pct,
        relative_day_pct=relative_day,
        relative_week_pct=relative_week,
        volume_24h_pct=volume_24h_pct,
        volume_7d_pct=volume_7d_pct,
        day_mark=day_mark,
        week_mark=week_mark,
        relative_mark=relative_mark,
        pressure=pressure,
        seasonality=seasonality,
        buy_count=buy_count,
        sell_count=sell_count,
        direction=direction,
        recommendation=recommendation,
        eligible=eligible,
        buy_flags=buy_flags,
        sell_flags=sell_flags,
    )


def _strength_count(item: CoinAnalysis) -> int:
    return item.buy_count if item.recommendation == "BUY" else item.sell_count


def format_line(item: CoinAnalysis, *, reference: bool = False) -> str:
    count = _strength_count(item)
    prefix = "" if reference else ("🟢 " if item.recommendation == "BUY" else "🔴 ")
    weekdays = "/".join(item.seasonality.best_weekdays) or "?"
    return (
        f"{prefix}{item.display_code} · {count}/8{item.direction} · "
        f"24h{item.day_mark} · 7d{item.week_mark} · vB{item.relative_mark} · "
        f"P{item.pressure} · N{item.seasonality.current} · {weekdays}"
    )


def build_report(
    reference: CoinAnalysis,
    grouped_analyses: list[list[CoinAnalysis]],
) -> str:
    lines = [format_line(reference, reference=True)]
    for group in grouped_analyses:
        buys = sorted(
            (item for item in group if item.eligible and item.recommendation == "BUY"),
            key=lambda item: (item.buy_count, mark_level(item.pressure), item.relative_day_pct),
            reverse=True,
        )
        sells = sorted(
            (item for item in group if item.eligible and item.recommendation == "SELL"),
            key=lambda item: (item.sell_count, -mark_level(item.pressure), -item.relative_day_pct),
            reverse=True,
        )
        lines.extend(format_line(item) for item in buys)
        lines.extend(format_line(item) for item in sells)
    return "\n".join(lines)


def analysis_to_dict(item: CoinAnalysis) -> dict[str, Any]:
    return asdict(item)
