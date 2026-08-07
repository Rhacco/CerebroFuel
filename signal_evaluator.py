# r3
"""Rolling NEAR/TRY/NOW outcome evaluation for CF v5.5.0."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

STATE_VERSION = "signal-evaluation-v550-r3"
TRACKED_ACTIONS = {"NEAR", "TRY", "NOW"}
HORIZONS = (3, 5, 10, 20)
RECENT_LIMIT = 200


def _minute_ms(signal: Any, now: datetime) -> int:
    candle_ms = int(getattr(signal, "candle_timestamp_ms", 0) or 0)
    if candle_ms > 0:
        return candle_ms - candle_ms % 60_000
    stamp_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
    return stamp_ms - stamp_ms % 60_000


def _direction(signal: Any) -> int:
    value = float(getattr(signal, "direction", 0.0) or 0.0)
    return 1 if value > 0 else -1 if value < 0 else 0


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": STATE_VERSION,
            "pending": [],
            "buckets": {},
            "recent_completed": [],
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if (
        raw.get("version") != STATE_VERSION
        or not isinstance(raw.get("pending"), list)
        or not isinstance(raw.get("buckets"), dict)
        or not isinstance(raw.get("recent_completed"), list)
    ):
        return {
            "version": STATE_VERSION,
            "pending": [],
            "buckets": {},
            "recent_completed": [],
        }
    return raw


def _save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _return_pct(start_price: float, current_price: float, direction: int) -> float:
    if start_price <= 0 or current_price <= 0:
        return 0.0
    return (current_price / start_price - 1.0) * 100.0 * direction


def _bucket_keys(row: dict[str, Any]) -> list[str]:
    symbol = str(row["symbol"])
    action = str(row["action"])
    setup = str(row["setup"] or "NONE")
    direction = "L" if int(row["direction"]) > 0 else "S"
    return [
        f"{symbol}|{action}|{setup}|{direction}",
        f"{symbol}|{action}|ALL|{direction}",
        f"ALL|{action}|{setup}|{direction}",
        f"ALL|{action}|ALL|{direction}",
    ]


def _update_bucket(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["samples"] = int(bucket.get("samples", 0)) + 1
    bucket["promotions"] = int(bucket.get("promotions", 0)) + int(bool(row.get("promoted_to")))
    bucket["reversals"] = int(bucket.get("reversals", 0)) + int(row.get("reversed_after") is not None)
    bucket["reversals_within_3m"] = int(bucket.get("reversals_within_3m", 0)) + int(
        row.get("reversed_after") is not None and int(row["reversed_after"]) <= 3
    )
    for name in ("direction_minutes", "action_minutes", "setup_minutes"):
        sum_key = "sum_" + name
        bucket[sum_key] = float(bucket.get(sum_key, 0.0)) + float(row.get(name, 0.0))
    horizons = bucket.setdefault("horizons", {})
    for horizon, outcome in (row.get("horizons") or {}).items():
        if not isinstance(outcome, dict) or outcome.get("return_pct") is None:
            continue
        stats = horizons.setdefault(str(horizon), {
            "samples": 0,
            "positive": 0,
            "sum_return_pct": 0.0,
            "sum_mfe_pct": 0.0,
            "sum_mae_pct": 0.0,
        })
        value = float(outcome["return_pct"])
        stats["samples"] = int(stats.get("samples", 0)) + 1
        stats["positive"] = int(stats.get("positive", 0)) + int(value > 0)
        stats["sum_return_pct"] = float(stats.get("sum_return_pct", 0.0)) + value
        stats["sum_mfe_pct"] = float(stats.get("sum_mfe_pct", 0.0)) + float(outcome.get("max_favorable_pct", 0.0))
        stats["sum_mae_pct"] = float(stats.get("sum_mae_pct", 0.0)) + float(outcome.get("max_adverse_pct", 0.0))


def _finalize(state: dict[str, Any], row: dict[str, Any]) -> None:
    completed = {
        key: row.get(key)
        for key in (
            "id", "symbol", "alias", "action", "direction", "setup",
            "started_minute_ms", "start_price", "max_favorable_pct",
            "max_adverse_pct", "direction_minutes", "action_minutes",
            "setup_minutes", "starting_streak", "promoted_to", "promotion_after",
            "reversed_after", "horizons",
        )
    }
    for key in _bucket_keys(row):
        bucket = state.setdefault("buckets", {}).setdefault(key, {})
        _update_bucket(bucket, row)
    recent = list(state.get("recent_completed") or [])
    recent.append(completed)
    state["recent_completed"] = recent[-RECENT_LIMIT:]


def update_signal_evaluation(
    signals: Iterable[Any],
    *,
    state_path: Path,
    now: datetime,
    action_getter: Callable[[Any], str],
) -> dict[str, Any]:
    """Evaluate each minute's visible action over 3/5/10/20-minute horizons."""
    state = _load(state_path)
    current = {
        str(getattr(signal, "symbol", "")).upper(): signal
        for signal in signals
        if str(getattr(signal, "symbol", "")).strip()
    }
    still_pending: list[dict[str, Any]] = []
    finalized = 0
    for source in list(state.get("pending") or []):
        if not isinstance(source, dict):
            continue
        row = dict(source)
        symbol = str(row.get("symbol", "")).upper()
        signal = current.get(symbol)
        current_minute = _minute_ms(signal, now) if signal is not None else int(
            now.astimezone(timezone.utc).timestamp() * 1000
        ) // 60_000 * 60_000
        elapsed = max(0, int((current_minute - int(row.get("started_minute_ms", 0))) // 60_000))
        action = str(action_getter(signal) or "") if signal is not None else ""
        direction = _direction(signal) if signal is not None else 0
        setup = str(getattr(signal, "selected_setup", "NONE") or "NONE") if signal is not None else "NONE"
        price = float(getattr(signal, "price", 0.0) or 0.0) if signal is not None else 0.0

        if price > 0:
            outcome = _return_pct(float(row.get("start_price", 0.0)), price, int(row.get("direction", 0)))
            row["max_favorable_pct"] = max(float(row.get("max_favorable_pct", 0.0)), outcome)
            row["max_adverse_pct"] = min(float(row.get("max_adverse_pct", 0.0)), outcome)
        else:
            outcome = None

        same_direction = action in TRACKED_ACTIONS and direction == int(row.get("direction", 0))
        if not bool(row.get("direction_broken", False)) and same_direction:
            row["direction_minutes"] = max(int(row.get("direction_minutes", 0)), elapsed)
        elif elapsed > 0:
            row["direction_broken"] = True

        same_action = same_direction and action == str(row.get("action", ""))
        if not bool(row.get("action_broken", False)) and same_action:
            row["action_minutes"] = max(int(row.get("action_minutes", 0)), elapsed)
        elif elapsed > 0:
            row["action_broken"] = True

        same_setup = same_direction and setup == str(row.get("setup", "NONE"))
        if not bool(row.get("setup_broken", False)) and same_setup:
            row["setup_minutes"] = max(int(row.get("setup_minutes", 0)), elapsed)
        elif elapsed > 0:
            row["setup_broken"] = True

        if (
            row.get("action") == "NEAR"
            and not row.get("promoted_to")
            and same_direction
            and action in {"TRY", "NOW"}
        ):
            row["promoted_to"] = action
            row["promotion_after"] = elapsed
        if (
            row.get("reversed_after") is None
            and action in TRACKED_ACTIONS
            and direction == -int(row.get("direction", 0))
        ):
            row["reversed_after"] = elapsed

        horizon_rows = dict(row.get("horizons") or {})
        for horizon in HORIZONS:
            key = str(horizon)
            if key in horizon_rows or elapsed < horizon or outcome is None:
                continue
            horizon_rows[key] = {
                "return_pct": round(float(outcome), 8),
                "action": action if action in TRACKED_ACTIONS else "WAIT",
                "direction": direction,
                "setup": setup,
                "observed_after_minutes": elapsed,
                "max_favorable_pct": round(float(row.get("max_favorable_pct", 0.0)), 8),
                "max_adverse_pct": round(float(row.get("max_adverse_pct", 0.0)), 8),
            }
        row["horizons"] = horizon_rows

        if elapsed >= max(HORIZONS):
            _finalize(state, row)
            finalized += 1
        else:
            still_pending.append(row)

    existing_ids = {str(row.get("id", "")) for row in still_pending if isinstance(row, dict)}
    created = 0
    for symbol, signal in current.items():
        action = str(action_getter(signal) or "")
        direction = _direction(signal)
        price = float(getattr(signal, "price", 0.0) or 0.0)
        minute_ms = _minute_ms(signal, now)
        row_id = f"{symbol}:{minute_ms}"
        if action not in TRACKED_ACTIONS or direction == 0 or price <= 0 or row_id in existing_ids:
            continue
        still_pending.append({
            "id": row_id,
            "symbol": symbol,
            "alias": str(getattr(signal, "alias", symbol[:3])),
            "action": action,
            "direction": direction,
            "setup": str(getattr(signal, "selected_setup", "NONE") or "NONE"),
            "started_minute_ms": minute_ms,
            "start_price": price,
            "max_favorable_pct": 0.0,
            "max_adverse_pct": 0.0,
            "direction_minutes": 0,
            "action_minutes": 0,
            "setup_minutes": 0,
            "direction_broken": False,
            "action_broken": False,
            "setup_broken": False,
            "promoted_to": None,
            "promotion_after": None,
            "reversed_after": None,
            "starting_streak": int(getattr(signal, "action_streak_count", 1) or 1),
            "horizons": {},
        })
        existing_ids.add(row_id)
        created += 1

    state.update({
        "version": STATE_VERSION,
        "updated_at": now.astimezone(timezone.utc).isoformat(),
        "pending": still_pending,
    })
    _save(state_path, state)
    return {
        "version": STATE_VERSION,
        "pending": len(still_pending),
        "completed_total": sum(int(bucket.get("samples", 0)) for key, bucket in state.get("buckets", {}).items() if key.startswith("ALL|") and "|ALL|" in key),
        "created": created,
        "finalized": finalized,
        "recent_completed": list(state.get("recent_completed") or [])[-10:],
        "buckets": state.get("buckets", {}),
    }


