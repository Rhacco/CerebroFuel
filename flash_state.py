"""v3.3.2 full-pool volume-priority flash scan.

A single LCW map response supplies fresh rate, rolling 24h volume and market cap
for every configured coin. Persisted five-minute observations turn that one
request into 10/30/60-minute volume and price trends for the complete pool.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

STATE_VERSION = "flash-v332-two-tail-unlock-r1"
WINDOWS = (10, 30, 60)
WINDOW_WEIGHTS = {10: 0.20, 30: 0.65, 60: 0.15}

PURPLE = "🟣"
GREEN = "🟢"
BLUE = "🔵"
YELLOW = "🟡"
ORANGE = "🟠"
RED = "🔴"


@dataclass(frozen=True)
class FlashSignal:
    display: str
    api_code: str
    score: float
    direction: str
    entry_score: float
    exit_score: float
    mismatch_score: float
    quality: float
    covered_windows: int
    price_changes: dict[int, float | None]
    volume_changes: dict[int, float | None]
    relative_changes: dict[int, float | None]
    divergence_30: float | None
    divergence_score: float
    volatility_score: float
    recovery_score: float
    recovery_color: str
    recent_crash_pct: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _delta_pct(value: Any) -> float:
    try:
        return (float(value) - 1.0) * 100.0
    except (TypeError, ValueError):
        return 0.0


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return 0.0
    position = _clamp(fraction) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _robust_center_scale(values: Sequence[float], fallback: float) -> tuple[float, float, int]:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    if not cleaned:
        return 0.0, max(fallback, 1e-6), 0
    center = statistics.median(cleaned)
    deviations = [abs(value - center) for value in cleaned]
    mad = statistics.median(deviations) if deviations else 0.0
    iqr = (_percentile(cleaned, 0.75) - _percentile(cleaned, 0.25)) / 1.349
    std = statistics.pstdev(cleaned) * 0.60 if len(cleaned) > 1 else 0.0
    scale = max(mad * 1.4826, iqr, std, fallback, 1e-6)
    return center, scale, len(cleaned)


def _z(value: float | None, baseline: tuple[float, float, int]) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    center, scale, _ = baseline
    return max(-6.0, min(6.0, (value - center) / max(scale, 1e-9)))


def _absolute_level(value: float, thresholds: tuple[float, float, float]) -> float:
    light, clear, strong = thresholds
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


def _thresholds(config: Mapping[str, Any], field: str, window: int) -> tuple[float, float, float]:
    raw = config.get(field, {}) if isinstance(config, Mapping) else {}
    item = raw.get(str(window), {}) if isinstance(raw, Mapping) else {}
    return (
        max(float(item.get("light", 0.05)), 1e-6),
        max(float(item.get("clear", 0.20)), 1e-6),
        max(float(item.get("strong", 0.75)), 1e-6),
    )


def _trend_strength(
    value: float | None,
    baseline: tuple[float, float, int],
    thresholds: tuple[float, float, float],
) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    absolute = _absolute_level(value, thresholds)
    z = _z(value, baseline) or 0.0
    direction = 1.0 if value >= 0 else -1.0
    unusual_same_direction = max(0.0, direction * z)
    magnitude = 0.62 * abs(absolute) + 0.38 * min(unusual_same_direction, 4.0)
    return direction * max(0.0, min(4.0, magnitude))


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("coins"), dict):
            return raw
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {"version": STATE_VERSION, "coins": {}}


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _clean_points(raw: Any, minimum_ms: int) -> list[list[float | int | None]]:
    points: dict[int, list[float | int | None]] = {}
    if not isinstance(raw, list):
        return []
    for item in raw:
        if not isinstance(item, list) or len(item) < 3:
            continue
        try:
            timestamp = int(item[0])
            rate = float(item[1])
            volume = None if item[2] is None else float(item[2])
        except (TypeError, ValueError):
            continue
        if timestamp < minimum_ms or rate <= 0 or not math.isfinite(rate):
            continue
        if volume is not None and (volume < 0 or not math.isfinite(volume)):
            volume = None
        points[timestamp] = [timestamp, rate, volume]
    return [points[key] for key in sorted(points)]


def _append_point(
    points: list[list[float | int | None]],
    *,
    now_ms: int,
    rate: float,
    volume: float | None,
) -> list[list[float | int | None]]:
    if points and abs(now_ms - int(points[-1][0])) <= 120_000:
        points[-1] = [now_ms, rate, volume]
    else:
        points.append([now_ms, rate, volume])
    return points


def _nearest(points: Sequence[Sequence[float | int | None]], target_ms: int, tolerance_ms: int):
    candidates = [point for point in points if abs(int(point[0]) - target_ms) <= tolerance_ms]
    if not candidates:
        return None
    return min(candidates, key=lambda point: abs(int(point[0]) - target_ms))


def _window_change(
    points: Sequence[Sequence[float | int | None]],
    *,
    end_point: Sequence[float | int | None],
    window: int,
) -> tuple[float | None, float | None]:
    end_ms = int(end_point[0])
    tolerance_minutes = max(4.5, min(11.0, window * 0.22))
    previous = _nearest(points, end_ms - window * 60_000, int(tolerance_minutes * 60_000))
    if previous is None or int(previous[0]) >= end_ms:
        return None, None
    price = _pct(float(end_point[1]), float(previous[1]))
    current_volume = None if end_point[2] is None else float(end_point[2])
    previous_volume = None if previous[2] is None else float(previous[2])
    volume = _pct(current_volume, previous_volume)
    return price, volume


def _rolling_samples(
    points: Sequence[Sequence[float | int | None]], window: int
) -> tuple[list[float], list[float]]:
    prices: list[float] = []
    volumes: list[float] = []
    minimum_spacing = max(5, window // 3) * 60_000
    last_endpoint = -10**30
    for point in points:
        timestamp = int(point[0])
        if timestamp - last_endpoint < minimum_spacing:
            continue
        price, volume = _window_change(points, end_point=point, window=window)
        if price is not None and math.isfinite(price):
            prices.append(price)
        if volume is not None and math.isfinite(volume) and abs(volume) <= 2000:
            volumes.append(volume)
        last_endpoint = timestamp
    return prices, volumes


def _fallback_scale(config: Mapping[str, Any], field: str, window: int) -> float:
    raw = config.get(field, {}) if isinstance(config, Mapping) else {}
    item = raw.get(str(window), {}) if isinstance(raw, Mapping) else {}
    return max(float(item.get("light", 0.05)), 1e-4) * 0.75


def _path_continuity(
    points: Sequence[Sequence[float | int | None]], *, now_ms: int, window: int, field_index: int
) -> float:
    start = now_ms - window * 60_000
    values = [
        float(point[field_index])
        for point in points
        if int(point[0]) >= start and point[field_index] is not None
    ]
    if len(values) < 3:
        return 0.55
    steps = [values[index] - values[index - 1] for index in range(1, len(values))]
    if not steps:
        return 0.55
    dominant = max(sum(step >= 0 for step in steps), sum(step <= 0 for step in steps))
    return dominant / len(steps)


def _volatility_score(points: Sequence[Sequence[float | int | None]], now_ms: int) -> float:
    cutoff = now_ms - 180 * 60_000
    recent = [point for point in points if int(point[0]) >= cutoff]
    returns: list[float] = []
    for previous, current in zip(recent, recent[1:]):
        change = _pct(float(current[1]), float(previous[1]))
        if change is not None and abs(change) <= 20:
            returns.append(change)
    if len(returns) < 4:
        return 0.0
    rms = math.sqrt(sum(value * value for value in returns) / len(returns))
    return 100.0 * _clamp((rms - 0.05) / 0.90)


def _recovery_metrics(
    points: Sequence[Sequence[float | int | None]],
    *,
    now_ms: int,
    price_30: float | None,
    volume_z: Mapping[int, float | None],
) -> tuple[float, str, float]:
    cutoff = now_ms - 360 * 60_000
    recent = [point for point in points if int(point[0]) >= cutoff]
    if len(recent) < 8:
        return 0.0, YELLOW, 0.0
    peak_index = max(range(len(recent)), key=lambda index: float(recent[index][1]))
    after_peak = recent[peak_index:]
    if len(after_peak) < 3:
        return 0.0, YELLOW, 0.0
    low_index_local = min(range(len(after_peak)), key=lambda index: float(after_peak[index][1]))
    low_index = peak_index + low_index_local
    peak = float(recent[peak_index][1])
    low = float(recent[low_index][1])
    current = float(recent[-1][1])
    crash_pct = (low / peak - 1.0) * 100.0 if peak > 0 else 0.0
    rebound_pct = (current / low - 1.0) * 100.0 if low > 0 else 0.0
    severity = _clamp((-crash_pct - 0.65) / 5.0)
    stable_price = _clamp(1.0 - abs(float(price_30 or 0.0)) / 1.20)
    after_low = low_index < len(recent) - 2
    rebound = _clamp(rebound_pct / max(-crash_pct * 0.35, 0.45)) if crash_pct < 0 else 0.0
    v10 = float(volume_z.get(10) or 0.0)
    v30 = float(volume_z.get(30) or 0.0)
    v60 = float(volume_z.get(60) or 0.0)
    volume_support = _clamp((0.45 * v10 + 0.40 * v30 + 0.15 * v60 + 0.25) / 2.30)
    stabilization = _clamp(0.52 * stable_price + 0.48 * rebound) if after_low else 0.0
    score = 100.0 * severity * (0.45 * stabilization + 0.55 * volume_support)
    ongoing = severity >= 0.25 and not after_low and float(price_30 or 0.0) < -0.35
    if score >= 78:
        color = PURPLE
    elif score >= 58:
        color = GREEN
    elif score >= 34:
        color = BLUE
    elif ongoing and volume_support < 0.30:
        color = RED
    elif ongoing:
        color = ORANGE
    else:
        color = YELLOW
    return score, color, crash_pct


def _map_fallback_score(row: Mapping[str, Any], btc: Mapping[str, Any]) -> tuple[float, str]:
    delta = row.get("delta") or {}
    btc_delta = btc.get("delta") or {}
    hour = _delta_pct(delta.get("hour"))
    relative_hour = hour - _delta_pct(btc_delta.get("hour"))
    volume = max(float(row.get("volume") or 0.0), 0.0)
    cap = max(float(row.get("cap") or 0.0), 0.0)
    turnover = volume / cap * 100.0 if cap > 0 else 0.0
    raw = abs(hour) * 3.0 + abs(relative_hour) * 2.0 + min(turnover, 30.0) * 0.15
    direction = "▲" if hour > 0.12 else ("▼" if hour < -0.12 else "=")
    return min(34.0, raw), direction


def _falling_knife_limits(config: Mapping[str, Any]) -> dict[int, float]:
    defaults = {10: -0.18, 30: -0.30, 60: -0.55}
    section = config.get("falling_knife_pct") if isinstance(config, Mapping) else None
    if not isinstance(section, Mapping):
        return defaults
    result: dict[int, float] = {}
    for window, fallback in defaults.items():
        try:
            result[window] = float(section.get(str(window), section.get(window, fallback)))
        except (TypeError, ValueError):
            result[window] = fallback
    return result


def _directional_volume_price_axis(price_strength: float, volume_strength: float) -> float:
    """Signed two-tail axis: volume minus price, with a falling-knife guard.

    Positive values require a broadly stable price and volume clearly outrunning
    price. A meaningfully falling price can never become a positive setup merely
    because volume rises; that combination confirms selling pressure.
    """
    price = float(price_strength)
    volume = float(volume_strength)
    if price <= -0.15:
        confirming_volume = max(volume, 0.0)
        volume_shortfall = max(price - volume, 0.0)
        axis = -(0.68 * abs(price) + 0.88 * confirming_volume + 0.55 * volume_shortfall)
    else:
        axis = volume - price
        if axis > 0.0 and price < -0.05:
            axis *= max(0.0, min(1.0, (price + 0.15) / 0.10))
    return max(-4.0, min(4.0, axis))

def _signal_for_coin(
    *,
    display: str,
    api_code: str,
    points: Sequence[Sequence[float | int | None]],
    btc_points: Sequence[Sequence[float | int | None]],
    row: Mapping[str, Any],
    btc_row: Mapping[str, Any],
    now_ms: int,
    config: Mapping[str, Any],
) -> FlashSignal:
    price_changes: dict[int, float | None] = {}
    volume_changes: dict[int, float | None] = {}
    relative_changes: dict[int, float | None] = {}
    price_z: dict[int, float | None] = {}
    volume_z: dict[int, float | None] = {}
    price_strengths: dict[int, float | None] = {}
    volume_strengths: dict[int, float | None] = {}
    gaps: dict[int, float | None] = {}
    covered = 0
    latest = points[-1] if points else None
    btc_latest = btc_points[-1] if btc_points else None

    for window in WINDOWS:
        price, volume = (None, None) if latest is None else _window_change(points, end_point=latest, window=window)
        btc_price, _ = (None, None) if btc_latest is None else _window_change(btc_points, end_point=btc_latest, window=window)
        price_changes[window] = price
        volume_changes[window] = volume
        relative_changes[window] = None if price is None or btc_price is None else price - btc_price
        if price is not None and volume is not None:
            covered += 1
        price_samples, volume_samples = _rolling_samples(points, window)
        price_baseline = _robust_center_scale(price_samples, _fallback_scale(config, "price", window))
        volume_baseline = _robust_center_scale(volume_samples, _fallback_scale(config, "volume", window))
        price_z[window] = _z(price, price_baseline)
        volume_z[window] = _z(volume, volume_baseline)
        price_strengths[window] = _trend_strength(price, price_baseline, _thresholds(config, "price", window))
        volume_strengths[window] = _trend_strength(volume, volume_baseline, _thresholds(config, "volume", window))
        if price_strengths[window] is not None and volume_strengths[window] is not None:
            gaps[window] = _directional_volume_price_axis(
                float(price_strengths[window]), float(volume_strengths[window])
            )
        else:
            gaps[window] = None

    quality = {0: 0.15, 1: 0.48, 2: 0.80, 3: 1.0}[covered]
    gap30 = gaps.get(30)
    available = [(float(gaps[w]), WINDOW_WEIGHTS[w]) for w in WINDOWS if gaps.get(w) is not None]
    weighted_gap = (
        sum(value * weight for value, weight in available) / sum(weight for _, weight in available)
        if available else 0.0
    )
    primary_axis = float(gap30) if gap30 is not None else weighted_gap
    knife_limits = _falling_knife_limits(config)
    falling_windows = sum(
        price_changes.get(window) is not None
        and float(price_changes[window]) < knife_limits[window]
        for window in WINDOWS
    )
    price30 = price_changes.get(30)
    if primary_axis > 0.0:
        if price30 is not None and float(price30) <= knife_limits[30]:
            primary_axis = -max(abs(primary_axis) * 0.45, min(3.2, abs(float(price30)) / 0.30))
        elif falling_windows >= 2:
            primary_axis = -max(abs(primary_axis) * 0.35, 0.22)
        elif price30 is not None and float(price30) < -0.08:
            primary_axis *= _clamp((float(price30) - knife_limits[30]) / (-0.08 - knife_limits[30]))
    gap30_score = 100.0 * _clamp(abs(primary_axis) / 3.20)
    corroboration = sum(
        (100.0 * _clamp(abs(float(gaps[w])) / 3.20)) * WINDOW_WEIGHTS[w]
        for w in WINDOWS if gaps.get(w) is not None
    )
    continuity = _path_continuity(points, now_ms=now_ms, window=30, field_index=2)
    volume_intensity = sum(
        abs(float(volume_strengths[w])) * WINDOW_WEIGHTS[w]
        for w in WINDOWS if volume_strengths.get(w) is not None
    )
    volume_support_score = 100.0 * _clamp(volume_intensity / 2.8)
    divergence_score = (
        0.72 * gap30_score + 0.18 * corroboration + 0.10 * volume_support_score
    ) * (0.78 + 0.22 * continuity)
    divergence_score *= 0.72 + 0.28 * quality
    divergence_score = max(0.0, min(100.0, divergence_score))

    direction = "▲" if primary_axis >= 0.18 else ("▼" if primary_axis <= -0.18 else "=")
    entry_score = divergence_score if direction == "▲" else 0.0
    exit_score = divergence_score if direction == "▼" else 0.0
    mismatch_score = divergence_score
    volatility = _volatility_score(points, now_ms)
    recovery_score, recovery_color, crash_pct = _recovery_metrics(
        points, now_ms=now_ms, price_30=price_changes.get(30), volume_z=volume_strengths
    )

    fallback_score, fallback_direction = _map_fallback_score(row, btc_row)
    score = max(divergence_score, fallback_score * (0.45 + 0.55 * quality))
    if direction == "=" and fallback_direction != "=":
        direction = fallback_direction

    reasons: list[str] = []
    if gap30 is not None:
        reasons.append(f"30m-Schere {gap30:+.2f}")
    if divergence_score >= 70:
        reasons.append("starke Volumen/Kurs-Divergenz")
    if recovery_score >= 34:
        reasons.append("Crash-Stabilisierung mit Volumen")
    if covered < 3:
        reasons.append(f"Warm-up {covered}/3")

    return FlashSignal(
        display=display,
        api_code=api_code,
        score=round(score, 4),
        direction=direction,
        entry_score=round(entry_score, 4),
        exit_score=round(exit_score, 4),
        mismatch_score=round(mismatch_score, 4),
        quality=round(quality, 4),
        covered_windows=covered,
        price_changes=price_changes,
        volume_changes=volume_changes,
        relative_changes=relative_changes,
        divergence_30=None if gap30 is None else round(float(gap30), 5),
        divergence_score=round(divergence_score, 4),
        volatility_score=round(volatility, 4),
        recovery_score=round(recovery_score, 4),
        recovery_color=recovery_color,
        recent_crash_pct=round(crash_pct, 4),
        reasons=tuple(reasons),
    )


def update_and_score(
    *,
    path: Path,
    resolved_pairs: Sequence[tuple[str, str]],
    current_by_code: Mapping[str, Mapping[str, Any]],
    reference_display: str,
    reference_api_code: str,
    now_ms: int,
    config: Mapping[str, Any],
) -> tuple[dict[str, FlashSignal], dict[str, Any]]:
    retention_minutes = int(config.get("flash_retention_minutes", 780))
    minimum_ms = now_ms - retention_minutes * 60_000
    previous = load_state(path)
    previous_coins = previous.get("coins") if isinstance(previous.get("coins"), dict) else {}
    coins: dict[str, dict[str, Any]] = {}

    for display, api_code in resolved_pairs:
        row = current_by_code.get(api_code)
        if row is None:
            continue
        old = previous_coins.get(display) if isinstance(previous_coins, dict) else None
        raw_points = old.get("points") if isinstance(old, dict) else None
        points = _clean_points(raw_points, minimum_ms)
        rate = float(row.get("rate") or 0.0)
        volume_raw = row.get("volume")
        volume = None if volume_raw in (None, "") else float(volume_raw)
        if rate <= 0:
            continue
        points = _append_point(points, now_ms=now_ms, rate=rate, volume=volume)
        coins[display] = {"api_code": api_code, "points": points}

    state = {
        "version": STATE_VERSION,
        "updated_at_ms": now_ms,
        "retention_minutes": retention_minutes,
        "coins": coins,
    }
    save_state(path, state)

    btc_entry = coins.get(reference_display)
    btc_row = current_by_code.get(reference_api_code)
    if not btc_entry or btc_row is None:
        return {}, {"coverage": 0.0, "coins": len(coins), "full_windows": 0}
    btc_points = btc_entry["points"]

    signals: dict[str, FlashSignal] = {}
    for display, api_code in resolved_pairs:
        entry = coins.get(display)
        row = current_by_code.get(api_code)
        if entry is None or row is None:
            continue
        signals[display] = _signal_for_coin(
            display=display,
            api_code=api_code,
            points=entry["points"],
            btc_points=btc_points,
            row=row,
            btc_row=btc_row,
            now_ms=now_ms,
            config=config,
        )

    full = sum(signal.covered_windows == 3 for signal in signals.values())
    average_coverage = (
        sum(signal.covered_windows for signal in signals.values()) / (len(signals) * 3.0)
        if signals else 0.0
    )
    return signals, {
        "coverage": round(average_coverage, 4),
        "coins": len(signals),
        "full_windows": full,
        "state_points": sum(len(item.get("points", [])) for item in coins.values()),
        "windows": list(WINDOWS),
    }
