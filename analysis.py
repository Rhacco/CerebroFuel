"""Deterministic market analysis and compact Discord formatting."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

DAY_NAMES = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]


@dataclass(frozen=True)
class PricePoint:
    timestamp_ms: int
    rate: float
    volume: float | None


@dataclass
class Seasonality:
    current: str
    preference: str
    best: str
    worst: str
    samples: int


@dataclass
class CoinAnalysis:
    code: str
    price: float
    hour_pct: float
    day_pct: float
    week_pct: float
    relative_day_pct: float
    relative_week_pct: float
    volume_ratio: float | None
    demand: str
    comeback: str
    recovery_position: float | None
    seasonality: Seasonality
    score: float
    signal: str
    signal_icon: str


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
        except (KeyError, TypeError, ValueError):
            continue
        by_timestamp[timestamp] = PricePoint(timestamp, rate, volume)
    return sorted(by_timestamp.values(), key=lambda point: point.timestamp_ms)


def _median(values: Iterable[float]) -> float | None:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.median(cleaned) if cleaned else None


def compute_volume_ratio(
    current_volume: float | None,
    points: list[PricePoint],
    now_ms: int,
) -> float | None:
    if current_volume is None or current_volume <= 0:
        return None
    cutoff = now_ms - 24 * 60 * 60 * 1000
    historical = [
        point.volume
        for point in points
        if point.timestamp_ms < cutoff and point.volume is not None and point.volume > 0
    ]
    baseline = _median(historical)
    if not baseline or baseline <= 0:
        return None
    return current_volume / baseline


def classify_demand(day_pct: float, volume_ratio: float | None) -> str:
    if volume_ratio is None:
        return "N?"
    if day_pct >= 1.0 and volume_ratio >= 1.5:
        return "N++"
    if day_pct > 0 and volume_ratio >= 1.1:
        return "N+"
    if day_pct <= -1.0 and volume_ratio >= 1.5:
        return "N--"
    if day_pct < 0 and volume_ratio >= 1.1:
        return "N-"
    return "N="


def classify_comeback(
    current_rate: float,
    day_pct: float,
    hour_pct: float,
    points: list[PricePoint],
    now_ms: int,
) -> tuple[str, float | None]:
    cutoff = now_ms - 24 * 60 * 60 * 1000
    recent = [point.rate for point in points if point.timestamp_ms >= cutoff]
    if len(recent) < 3:
        return "CB?", None

    low = min(recent)
    high = max(recent)
    spread = high - low
    if spread <= 0:
        return "CB=", 0.5

    position = max(0.0, min(1.0, (current_rate - low) / spread))
    drawdown_from_high = (current_rate / high - 1.0) * 100.0

    if day_pct > 0 and hour_pct > 0 and position >= 0.80:
        return "CB++", position
    if day_pct > 0 and position >= 0.65:
        return "CB+", position
    if position >= 0.50 and day_pct <= 0:
        return "CB?", position
    if position < 0.35 and day_pct < 0:
        return "CB-", position
    if drawdown_from_high <= -4.0 and hour_pct < 0:
        return "CB-", position
    return "CB=", position


def _return_observations(
    points: list[PricePoint], timezone: str, block_hours: int
) -> list[tuple[int, int, float]]:
    tz = ZoneInfo(timezone)
    observations: list[tuple[int, int, float]] = []

    for previous, current in zip(points, points[1:]):
        elapsed_hours = (current.timestamp_ms - previous.timestamp_ms) / 3_600_000
        if elapsed_hours < 0.08 or elapsed_hours > 6.5:
            continue
        if previous.rate <= 0 or current.rate <= 0:
            continue

        # Log return normalized to one hour, so differently spaced API points remain comparable.
        hourly_return = math.log(current.rate / previous.rate) * 100.0 / elapsed_hours
        local_dt = datetime.fromtimestamp(current.timestamp_ms / 1000, tz=tz)
        block = (local_dt.hour // block_hours) * block_hours
        observations.append((local_dt.weekday(), block, hourly_return))

    return observations


def _slot_label(weekday: int, block: int, block_hours: int) -> str:
    end = (block + block_hours) % 24
    return f"{DAY_NAMES[weekday]} {block:02d}–{end:02d}h"


def analyze_seasonality(
    points: list[PricePoint],
    now: datetime,
    timezone: str,
    block_hours: int = 4,
    min_samples: int = 3,
) -> Seasonality:
    observations = _return_observations(points, timezone, block_hours)
    if len(observations) < 20:
        return Seasonality("TZ?", "WT=WE", "Lernphase", "Lernphase", len(observations))

    grouped: dict[tuple[int, int], list[float]] = {}
    workday: list[float] = []
    weekend: list[float] = []
    all_returns: list[float] = []

    for weekday, block, value in observations:
        grouped.setdefault((weekday, block), []).append(value)
        (workday if weekday < 5 else weekend).append(value)
        all_returns.append(value)

    eligible = {
        slot: values for slot, values in grouped.items() if len(values) >= min_samples
    }
    if not eligible:
        return Seasonality("TZ?", "WT=WE", "Lernphase", "Lernphase", len(observations))

    def quality(values: list[float]) -> float:
        avg = statistics.mean(values)
        hit_rate = sum(value > 0 for value in values) / len(values)
        return avg * (0.5 + hit_rate)

    best_slot = max(eligible, key=lambda slot: quality(eligible[slot]))
    worst_slot = min(eligible, key=lambda slot: quality(eligible[slot]))

    local_now = now.astimezone(ZoneInfo(timezone))
    current_block = (local_now.hour // block_hours) * block_hours
    current_values = eligible.get((local_now.weekday(), current_block), [])

    median_abs = _median(abs(value) for value in all_returns) or 0.0
    threshold = max(0.025, median_abs * 0.15)

    if current_values:
        current_avg = statistics.mean(current_values)
        current_hit = sum(value > 0 for value in current_values) / len(current_values)
        if current_avg > threshold and current_hit >= 0.56:
            current_label = "TZ+"
        elif current_avg < -threshold and current_hit <= 0.44:
            current_label = "TZ⚠"
        else:
            current_label = "TZ="
    else:
        current_label = "TZ?"

    if workday and weekend:
        workday_avg = statistics.mean(workday)
        weekend_avg = statistics.mean(weekend)
        difference = workday_avg - weekend_avg
        if difference > threshold * 0.35:
            preference = "WT>WE"
        elif difference < -threshold * 0.35:
            preference = "WE>WT"
        else:
            preference = "WT=WE"
    else:
        preference = "WT=WE"

    return Seasonality(
        current=current_label,
        preference=preference,
        best=_slot_label(best_slot[0], best_slot[1], block_hours),
        worst=_slot_label(worst_slot[0], worst_slot[1], block_hours),
        samples=len(observations),
    )


def _graded(value: float, small: float, large: float) -> float:
    if value >= large:
        return 1.0
    if value >= small:
        return 0.5
    if value <= -large:
        return -1.0
    if value <= -small:
        return -0.5
    return 0.0


def calculate_signal_score(
    *,
    hour_pct: float,
    day_pct: float,
    week_pct: float,
    relative_day_pct: float,
    relative_week_pct: float,
    demand: str,
    comeback: str,
    seasonality_current: str,
    btc_day_pct: float,
    btc_week_pct: float,
    volume_ratio: float | None,
) -> float:
    score = 0.0
    score += 0.5 * _graded(hour_pct, 0.15, 0.8)
    score += 0.6 * _graded(day_pct, 0.5, 2.0)
    score += 0.8 * _graded(week_pct, 1.5, 5.0)
    score += 1.0 * _graded(relative_day_pct, 0.4, 1.5)
    score += 1.2 * _graded(relative_week_pct, 1.0, 4.0)

    score += {"N++": 1.0, "N+": 0.5, "N=": 0.0, "N?": 0.0, "N-": -0.5, "N--": -1.0}[demand]
    score += {"CB++": 0.8, "CB+": 0.4, "CB=": 0.0, "CB?": -0.1, "CB-": -0.8}[comeback]
    score += {"TZ+": 0.25, "TZ=": 0.0, "TZ?": 0.0, "TZ⚠": -0.25}[seasonality_current]

    if btc_day_pct <= -3.0 or btc_week_pct <= -8.0:
        score -= 0.6
    elif btc_day_pct >= 2.0 and btc_week_pct >= 0:
        score += 0.25

    # Risk overrides: abrupt weakness should not be hidden by older positive data.
    if hour_pct <= -2.5:
        score = min(score, -3.0)
    if day_pct <= -6.0 and relative_day_pct < 0:
        score = min(score, -3.0)

    # Overheated moves receive a penalty instead of an automatic entry signal.
    if day_pct >= 9.0 and volume_ratio is not None and volume_ratio >= 2.5:
        score -= 1.2

    return round(score, 2)


def signal_from_score(score: float, entry: float, exit_: float) -> tuple[str, str]:
    if score >= entry:
        return "EIN", "🟢"
    if score <= exit_:
        return "AUS", "🔴"
    return "WARTEN", "🟡"


def analyze_coin(
    *,
    code: str,
    current: dict[str, Any],
    history: list[PricePoint],
    btc_day_pct: float,
    btc_week_pct: float,
    now: datetime,
    timezone: str,
    block_hours: int,
    min_samples: int,
    entry_threshold: float,
    exit_threshold: float,
) -> CoinAnalysis:
    delta = current.get("delta") or {}
    price = float(current["rate"])
    hour_pct = delta_to_pct(delta.get("hour"))
    day_pct = delta_to_pct(delta.get("day"))
    week_pct = delta_to_pct(delta.get("week"))
    relative_day = day_pct - btc_day_pct
    relative_week = week_pct - btc_week_pct
    now_ms = int(now.timestamp() * 1000)

    volume_raw = current.get("volume")
    current_volume = float(volume_raw) if volume_raw not in (None, "") else None
    volume_ratio = compute_volume_ratio(current_volume, history, now_ms)
    demand = classify_demand(day_pct, volume_ratio)
    comeback, recovery_position = classify_comeback(
        price, day_pct, hour_pct, history, now_ms
    )
    seasonality = analyze_seasonality(
        history, now, timezone, block_hours, min_samples
    )
    score = calculate_signal_score(
        hour_pct=hour_pct,
        day_pct=day_pct,
        week_pct=week_pct,
        relative_day_pct=relative_day,
        relative_week_pct=relative_week,
        demand=demand,
        comeback=comeback,
        seasonality_current=seasonality.current,
        btc_day_pct=btc_day_pct,
        btc_week_pct=btc_week_pct,
        volume_ratio=volume_ratio,
    )
    signal, icon = signal_from_score(score, entry_threshold, exit_threshold)

    return CoinAnalysis(
        code=code,
        price=price,
        hour_pct=hour_pct,
        day_pct=day_pct,
        week_pct=week_pct,
        relative_day_pct=relative_day,
        relative_week_pct=relative_week,
        volume_ratio=volume_ratio,
        demand=demand,
        comeback=comeback,
        recovery_position=recovery_position,
        seasonality=seasonality,
        score=score,
        signal=signal,
        signal_icon=icon,
    )


def market_arrow(btc_day_pct: float, btc_week_pct: float) -> str:
    if btc_day_pct >= 1.0 or btc_week_pct >= 3.0:
        return "↗"
    if btc_day_pct <= -1.0 or btc_week_pct <= -3.0:
        return "↘"
    return "→"


def format_pct(value: float) -> str:
    return f"{value:+.1f}"


def format_price(value: float) -> str:
    if value >= 100_000:
        return f"${value / 1000:.1f}k"
    if value >= 1_000:
        return f"${value / 1000:.2f}k"
    if value >= 100:
        return f"${value:.2f}"
    if value >= 1:
        return f"${value:.3f}"
    if value >= 0.01:
        return f"${value:.4f}"
    if value >= 0.001:
        return f"${value:.5f}"
    if value >= 0.000001:
        return f"${value:.7f}"
    return f"${value:.2e}"


def build_report(
    *,
    now: datetime,
    timezone: str,
    reference_code: str,
    reference_current: dict[str, Any],
    analyses: list[CoinAnalysis],
    history_days: int,
) -> str:
    local_now = now.astimezone(ZoneInfo(timezone))
    ref_delta = reference_current.get("delta") or {}
    btc_day = delta_to_pct(ref_delta.get("day"))
    btc_week = delta_to_pct(ref_delta.get("week"))

    lines = [
        f"📊 {local_now:%d.%m %H:%M} | {reference_code} {format_pct(btc_day)}/{format_pct(btc_week)} | MKT{market_arrow(btc_day, btc_week)}"
    ]

    for item in analyses:
        lines.append(
            f"{item.code} {format_price(item.price)} | {format_pct(item.day_pct)}/{format_pct(item.week_pct)} "
            f"| vsB {format_pct(item.relative_day_pct)}/{format_pct(item.relative_week_pct)} "
            f"| {item.demand} {item.comeback} {item.seasonality.current} {item.seasonality.preference} "
            f"| {item.signal_icon}{item.signal}"
        )

    time_parts = [
        f"{item.code}: +{item.seasonality.best} / ⚠{item.seasonality.worst}"
        for item in analyses
    ]
    lines.append(f"⏱ {' · '.join(time_parts)} | {history_days}T")
    lines.append("24h/7d · autom. technisches Signal, keine Anlageberatung · Daten: Live Coin Watch")
    return "\n".join(lines)


def analysis_to_dict(item: CoinAnalysis) -> dict[str, Any]:
    return asdict(item)
