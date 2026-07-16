"""v3.2.9 map-snapshot flash scan for every configured coin.

One /coins/map response already contains the fresh rate and rolling 24h volume for
all configured coins. This module persists those map observations between runs and
uses them to rank 10/20/60-minute entry and exit proximity for the complete pool
without additional LiveCoinWatch credits.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

STATE_VERSION = "flash-v329-r1"
WINDOWS = (10, 20, 60)
WINDOW_WEIGHTS = {10: 0.50, 20: 0.32, 60: 0.18}


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
    mad_scale = mad * 1.4826
    iqr_scale = (_percentile(cleaned, 0.75) - _percentile(cleaned, 0.25)) / 1.349
    std_scale = statistics.pstdev(cleaned) * 0.60 if len(cleaned) > 1 else 0.0
    candidates = [value for value in (mad_scale, iqr_scale, std_scale) if value > 0]
    scale = max(min(candidates) if candidates else 0.0, fallback, 1e-6)
    return center, scale, len(cleaned)


def _z(value: float | None, baseline: tuple[float, float, int]) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    center, scale, _ = baseline
    return max(-6.0, min(6.0, (value - center) / max(scale, 1e-9)))


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
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
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
    # Manual retries within two minutes replace the last observation instead of
    # creating an artificial high-frequency step.
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
    tolerance_minutes = max(4.5, min(10.0, window * 0.22))
    previous = _nearest(points, end_ms - window * 60_000, int(tolerance_minutes * 60_000))
    if previous is None or int(previous[0]) >= end_ms:
        return None, None
    price = _pct(float(end_point[1]), float(previous[1]))
    current_volume = None if end_point[2] is None else float(end_point[2])
    previous_volume = None if previous[2] is None else float(previous[2])
    volume = _pct(current_volume, previous_volume)
    return price, volume


def _rolling_samples(
    points: Sequence[Sequence[float | int | None]],
    window: int,
) -> tuple[list[float], list[float]]:
    price_samples: list[float] = []
    volume_samples: list[float] = []
    last_endpoint = -10**30
    minimum_spacing = max(5, window // 3) * 60_000
    for point in points:
        timestamp = int(point[0])
        if timestamp - last_endpoint < minimum_spacing:
            continue
        price, volume = _window_change(points, end_point=point, window=window)
        if price is not None and math.isfinite(price):
            price_samples.append(price)
        if volume is not None and math.isfinite(volume) and abs(volume) <= 2000:
            volume_samples.append(volume)
        last_endpoint = timestamp
    return price_samples, volume_samples


def _path_quality(
    points: Sequence[Sequence[float | int | None]],
    *,
    now_ms: int,
    window: int,
    field_index: int,
    expected_sign: int,
) -> tuple[float, float]:
    start_ms = now_ms - window * 60_000
    values: list[tuple[int, float]] = []
    for point in points:
        timestamp = int(point[0])
        raw = point[field_index]
        if timestamp < start_ms or timestamp > now_ms or raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append((timestamp, value))
    if len(values) < 3:
        return 0.45, 1.0
    steps = [values[index][1] - values[index - 1][1] for index in range(1, len(values))]
    signed = [step * expected_sign for step in steps]
    continuity = sum(step >= 0 for step in signed) / len(signed)
    total = sum(abs(step) for step in steps)
    jump_share = max((abs(step) for step in steps), default=0.0) / total if total > 0 else 0.0
    return continuity, jump_share


def _fallback_scale(config: Mapping[str, Any], field: str, window: int) -> float:
    raw = config.get(field, {}) if isinstance(config, Mapping) else {}
    item = raw.get(str(window), {}) if isinstance(raw, Mapping) else {}
    return max(float(item.get("light", 0.05)), 1e-4) * 0.75


def _map_fallback_score(row: Mapping[str, Any], btc: Mapping[str, Any]) -> tuple[float, str]:
    delta = row.get("delta") or {}
    btc_delta = btc.get("delta") or {}
    hour = _delta_pct(delta.get("hour"))
    day = _delta_pct(delta.get("day"))
    week = _delta_pct(delta.get("week"))
    relative_hour = hour - _delta_pct(btc_delta.get("hour"))
    relative_day = day - _delta_pct(btc_delta.get("day"))
    volume = max(float(row.get("volume") or 0.0), 0.0)
    cap = max(float(row.get("cap") or 0.0), 0.0)
    turnover = volume / cap * 100.0 if cap > 0 else 0.0
    raw = (
        abs(hour) * 4.0
        + abs(relative_hour) * 3.0
        + abs(day) * 0.6
        + abs(relative_day) * 0.5
        + abs(week) * 0.08
        + min(turnover, 30.0) * 0.18
    )
    direction = "▲" if hour + relative_hour * 0.5 > 0.15 else ("▼" if hour + relative_hour * 0.5 < -0.15 else "=")
    return min(48.0, raw), direction


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
    covered = 0
    latest = points[-1] if points else None
    btc_latest = btc_points[-1] if btc_points else None

    for window in WINDOWS:
        if latest is None:
            price, volume = None, None
        else:
            price, volume = _window_change(points, end_point=latest, window=window)
        if btc_latest is None:
            btc_price = None
        else:
            btc_price, _ = _window_change(btc_points, end_point=btc_latest, window=window)
        price_changes[window] = price
        volume_changes[window] = volume
        relative_changes[window] = None if price is None or btc_price is None else price - btc_price
        if price is not None and volume is not None:
            covered += 1
        p_samples, v_samples = _rolling_samples(points, window)
        price_baseline = _robust_center_scale(p_samples, _fallback_scale(config, "price", window))
        volume_baseline = _robust_center_scale(v_samples, _fallback_scale(config, "volume", window))
        price_z[window] = _z(price, price_baseline)
        volume_z[window] = _z(volume, volume_baseline)

    quality = {0: 0.18, 1: 0.52, 2: 0.82, 3: 1.0}[covered]
    entry_windows: dict[int, float] = {}
    exit_windows: dict[int, float] = {}
    mismatch_windows: dict[int, float] = {}
    reasons: list[str] = []

    for window in WINDOWS:
        pz = price_z.get(window)
        vz = volume_z.get(window)
        if pz is None or vz is None:
            entry_windows[window] = 0.0
            exit_windows[window] = 0.0
            mismatch_windows[window] = 0.0
            continue
        price_flat = _clamp(1.0 - abs(pz) / 1.55)
        price_nonnegative = _clamp((pz + 0.30) / 1.80)
        price_positive = _clamp((pz - 0.10) / 2.30)
        price_negative = _clamp((-pz - 0.15) / 2.20)
        volume_surge = _clamp((vz - 0.35) / 2.50)
        volume_collapse = _clamp((-vz - 0.35) / 2.50)
        volume_lead = _clamp((vz - max(pz, 0.0) * 0.55 - 0.25) / 2.40)
        support_loss = _clamp((pz - vz - 0.45) / 2.40)
        sell_pressure = price_negative * _clamp((vz - 0.20) / 2.25)

        v_cont, v_jump = _path_quality(
            points, now_ms=now_ms, window=window, field_index=2, expected_sign=1
        )
        p_cont, _ = _path_quality(
            points, now_ms=now_ms, window=window, field_index=1, expected_sign=1
        )
        path_factor = (0.58 + 0.42 * v_cont) * (0.66 if v_jump > 0.82 else (0.84 if v_jump > 0.70 else 1.0))
        entry_windows[window] = 100.0 * volume_surge * (0.72 * price_flat + 0.28 * price_nonnegative)
        entry_windows[window] *= (0.70 + 0.30 * volume_lead) * path_factor

        # Distribution/support-loss reacts fast: a rising/high price with collapsing
        # or lagging volume is the intended immediate SELL pattern.
        collapse_exit = volume_collapse * (0.55 + 0.45 * max(price_positive, price_flat))
        divergence_exit = support_loss * (0.45 + 0.55 * price_positive) * (0.62 + 0.38 * p_cont)
        exit_windows[window] = 100.0 * max(collapse_exit, divergence_exit, sell_pressure)
        mismatch_windows[window] = 100.0 * max(
            min(volume_surge, price_negative),
            min(volume_collapse, price_positive),
            0.70 * support_loss,
        )

    entry_raw = sum(entry_windows[w] * WINDOW_WEIGHTS[w] for w in WINDOWS)
    exit_raw = sum(exit_windows[w] * WINDOW_WEIGHTS[w] for w in WINDOWS)
    mismatch_raw = sum(mismatch_windows[w] * WINDOW_WEIGHTS[w] for w in WINDOWS)

    delta = row.get("delta") or {}
    btc_delta = btc_row.get("delta") or {}
    week = _delta_pct(delta.get("week"))
    day = _delta_pct(delta.get("day"))
    rel_hour = _delta_pct(delta.get("hour")) - _delta_pct(btc_delta.get("hour"))
    rel_week = week - _delta_pct(btc_delta.get("week"))
    context_entry = 0.58
    context_entry += 0.12 if week > 0 else 0.0
    context_entry += 0.10 if day > 0 else 0.0
    context_entry += 0.10 if rel_hour > 0 else 0.0
    context_entry += 0.10 if rel_week > 0 else 0.0

    # Memory: a coin that was supported earlier and now loses volume deserves an
    # immediate high-priority exit review.
    prior_memory = 0.0
    if latest is not None:
        p20 = _nearest(points, now_ms - 20 * 60_000, 6 * 60_000)
        p60 = _nearest(points, now_ms - 60 * 60_000, 10 * 60_000)
        if p20 is not None and p60 is not None:
            prior_price = _pct(float(p20[1]), float(p60[1])) or 0.0
            prior_volume = _pct(
                None if p20[2] is None else float(p20[2]),
                None if p60[2] is None else float(p60[2]),
            ) or 0.0
            price_clear = max(_fallback_scale(config, "price", 60) * 2.5, 0.25)
            volume_clear = max(_fallback_scale(config, "volume", 60) * 2.5, 0.35)
            prior_memory = _clamp(max(prior_price / price_clear, prior_volume / volume_clear) / 2.0)

    entry_score = _clamp(entry_raw * context_entry / 100.0) * 100.0
    exit_score = _clamp((exit_raw / 100.0) * (0.82 + 0.28 * prior_memory)) * 100.0
    mismatch_score = _clamp(mismatch_raw / 100.0) * 100.0

    fallback_score, fallback_direction = _map_fallback_score(row, btc_row)
    attention = max(entry_score, exit_score, mismatch_score) * quality
    # During the first hour after installation, map data keeps the whole pool
    # ranked, but cannot create a false extreme without snapshot confirmation.
    score = max(attention, fallback_score * (0.55 + 0.45 * quality))
    direction = "▲" if entry_score > exit_score + 5.0 else ("▼" if exit_score > entry_score + 5.0 else fallback_direction)

    if entry_score >= 70:
        reasons.append("Volumen führt bei ruhigem Kurs")
    if exit_score >= 65:
        reasons.append("Volumen-Supportverlust")
    if mismatch_score >= 55:
        reasons.append("Kurs/Volumen-Divergenz")
    if covered < 3:
        reasons.append(f"Warm-up {covered}/3")

    return FlashSignal(
        display=display,
        api_code=api_code,
        score=round(min(100.0, score), 4),
        direction=direction,
        entry_score=round(entry_score, 4),
        exit_score=round(exit_score, 4),
        mismatch_score=round(mismatch_score, 4),
        quality=round(quality, 4),
        covered_windows=covered,
        price_changes=price_changes,
        volume_changes=volume_changes,
        relative_changes=relative_changes,
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
        if signals
        else 0.0
    )
    return signals, {
        "coverage": round(average_coverage, 4),
        "coins": len(signals),
        "full_windows": full,
        "state_points": sum(len(item.get("points", [])) for item in coins.values()),
    }
