"""Live, setup-specific +3%/+5% outcome memory for v3.3.3.

Every five-minute run can resolve previously recorded entry setups using fresh
LCW map prices.  New setup events are rate-limited per coin.  The resulting
statistics start neutral and only influence ranking after enough real outcomes.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

STATE_VERSION = "opportunity-v333-target-r2"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _wilson_lower(successes: int, total: int, z: float = 1.2816) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return _clamp((centre - margin) / denominator)


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("events"), list):
            return raw
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {"version": STATE_VERSION, "events": [], "last_signal_ms": {}}


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _profile(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    resolved = [event for event in events if event.get("result3") and event.get("result5")]
    total = len(resolved)
    if total == 0:
        return {
            "score": 50.0,
            "confidence": 0.0,
            "samples": 0,
            "hit3_before_stop": None,
            "hit5_before_stop": None,
            "median_hours_to_3": None,
            "median_hours_to_5": None,
            "method": "live-setup-memory-empty",
        }
    hit3 = sum(event.get("result3") == "hit" for event in resolved)
    hit5 = sum(event.get("result5") == "hit" for event in resolved)
    times3 = [float(event["hours3"]) for event in resolved if event.get("result3") == "hit" and event.get("hours3") is not None]
    times5 = [float(event["hours5"]) for event in resolved if event.get("result5") == "hit" and event.get("hours5") is not None]
    lower3 = _wilson_lower(hit3, total)
    lower5 = _wilson_lower(hit5, total)
    speed3 = 0.0 if not times3 else _clamp((24.0 - statistics.median(times3)) / 20.0)
    speed5 = 0.0 if not times5 else _clamp((24.0 - statistics.median(times5)) / 20.0)
    evidence = 100.0 * (0.58 * lower3 + 0.30 * lower5 + 0.08 * speed3 + 0.04 * speed5)
    confidence = _clamp((total - 2.0) / 12.0)
    score = 50.0 + (evidence - 50.0) * confidence
    return {
        "score": round(_clamp(score / 100.0) * 100.0, 4),
        "confidence": round(confidence, 4),
        "samples": total,
        "hit3": hit3,
        "hit5": hit5,
        "hit3_before_stop": round(hit3 / total, 5),
        "hit5_before_stop": round(hit5 / total, 5),
        "wilson3": round(lower3, 5),
        "wilson5": round(lower5, 5),
        "median_hours_to_3": None if not times3 else round(statistics.median(times3), 3),
        "median_hours_to_5": None if not times5 else round(statistics.median(times5), 3),
        "method": "live-setup-candle-range-target-before-stop-r2",
    }


def update_and_resolve(
    *,
    path: Path,
    prices: Mapping[str, float],
    candle_ranges: Mapping[str, Mapping[str, float | int | None]] | None = None,
    now_ms: int,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    state = load_state(path)
    raw_events = state.get("events") if isinstance(state.get("events"), list) else []
    horizon_hours = float(config.get("target_horizon_hours", 24))
    horizon_ms = int(max(1.0, horizon_hours) * 3_600_000)
    retention_days = max(14, int(config.get("outcome_retention_days", 90)))
    cutoff_ms = now_ms - retention_days * 86_400_000
    events: list[dict[str, Any]] = []
    newly_resolved = 0

    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        event = dict(raw)
        try:
            created = int(event.get("created_ms"))
            entry = float(event.get("entry_price"))
            display = str(event.get("display") or "").upper()
        except (TypeError, ValueError):
            continue
        if not display or entry <= 0 or created < cutoff_ms:
            continue
        current = prices.get(display)
        if current is not None and float(current) > 0 and not (event.get("result3") and event.get("result5")):
            current_value = float(current)
            range_item = (candle_ranges or {}).get(display) or {}
            range_open_ms = range_item.get("open_ms")
            range_is_new = range_open_ms is not None and int(range_open_ms) >= created
            high_value = float(range_item.get("high") or current_value) if range_is_new else current_value
            low_value = float(range_item.get("low") or current_value) if range_is_new else current_value
            high_change = (max(current_value, high_value) / entry - 1.0) * 100.0
            low_change = (min(current_value, low_value) / entry - 1.0) * 100.0
            event["max_return_pct"] = round(max(float(event.get("max_return_pct", high_change)), high_change), 5)
            event["min_return_pct"] = round(min(float(event.get("min_return_pct", low_change)), low_change), 5)
            age_hours = max(0.0, (now_ms - created) / 3_600_000.0)
            # Candle highs/lows avoid missing a target or stop that happened
            # between two five-minute runs. If both lie inside the same candle,
            # order is unknowable, therefore resolve conservatively as stop first.
            if not event.get("result3"):
                if low_change <= -1.5:
                    event["result3"] = "stop"
                    event["hours3"] = round(age_hours, 4)
                elif high_change >= 3.0:
                    event["result3"] = "hit"
                    event["hours3"] = round(age_hours, 4)
                elif now_ms - created >= horizon_ms:
                    event["result3"] = "timeout"
                    event["hours3"] = round(age_hours, 4)
            if not event.get("result5"):
                if low_change <= -2.0:
                    event["result5"] = "stop"
                    event["hours5"] = round(age_hours, 4)
                elif high_change >= 5.0:
                    event["result5"] = "hit"
                    event["hours5"] = round(age_hours, 4)
                elif now_ms - created >= horizon_ms:
                    event["result5"] = "timeout"
                    event["hours5"] = round(age_hours, 4)
            if event.get("result3") and event.get("result5") and not raw.get("resolved_ms"):
                event["resolved_ms"] = now_ms
                newly_resolved += 1
        events.append(event)

    by_coin: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_coin.setdefault(str(event.get("display") or "").upper(), []).append(event)
    profiles = {display: _profile(items) for display, items in by_coin.items() if display}
    state = {
        "version": STATE_VERSION,
        "updated_at_ms": now_ms,
        "events": events[-600:],
        "last_signal_ms": dict(state.get("last_signal_ms") or {}),
    }
    save_state(path, state)
    return state, profiles, {
        "events": len(events),
        "pending": sum(not (event.get("result3") and event.get("result5")) for event in events),
        "newly_resolved": newly_resolved,
        "profiled_coins": len(profiles),
    }


def record_entry_candidates(
    *,
    path: Path,
    state: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    prices: Mapping[str, float],
    now_ms: int,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    events = [dict(item) for item in state.get("events", []) if isinstance(item, Mapping)]
    last_signal = {
        str(key).upper(): int(value)
        for key, value in (state.get("last_signal_ms") or {}).items()
        if str(key).strip()
    }
    minimum_score = float(config.get("outcome_record_minimum_entry_score", 60.0))
    cooldown_ms = int(float(config.get("outcome_signal_cooldown_hours", 6.0)) * 3_600_000)
    created = 0
    for item in candidates:
        display = str(item.get("display") or "").upper()
        entry_score = float(item.get("entry_score") or 0.0)
        if (
            not display
            or entry_score < minimum_score
            or bool(item.get("falling_knife"))
            or bool(item.get("late_entry"))
            or now_ms - int(last_signal.get(display, -10**30)) < cooldown_ms
        ):
            continue
        price = float(prices.get(display) or 0.0)
        if price <= 0:
            continue
        events.append(
            {
                "display": display,
                "created_ms": now_ms,
                "entry_price": price,
                "entry_score": round(entry_score, 4),
                "provider": item.get("provider"),
                "base_quality": round(float(item.get("base_quality_score") or 0.0), 4),
                "demand_score": round(float(item.get("demand_score") or 0.0), 4),
                "target_prior_score": round(float(item.get("target_prior_score") or 50.0), 4),
                "result3": None,
                "result5": None,
                "hours3": None,
                "hours5": None,
                "max_return_pct": 0.0,
                "min_return_pct": 0.0,
            }
        )
        last_signal[display] = now_ms
        created += 1

    new_state = {
        "version": STATE_VERSION,
        "updated_at_ms": now_ms,
        "events": events[-600:],
        "last_signal_ms": last_signal,
    }
    save_state(path, new_state)
    return {"created": created, "events": len(new_state["events"])}
