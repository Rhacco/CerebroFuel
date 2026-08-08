# r1
"""Persistent action-state streaks for CF v5.7.0 detail tokens."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

STATE_VERSION = "signal-streak-v570-r1"
TRACKED_ACTIONS = {"NEAR", "TRY", "NOW"}
MAX_COUNT = 999


def _minute_ms(signal: Any, now: datetime) -> int:
    candle_ms = int(getattr(signal, "candle_timestamp_ms", 0) or 0)
    if candle_ms > 0:
        return candle_ms - candle_ms % 60_000
    stamp_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
    return stamp_ms - stamp_ms % 60_000


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "entries": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "entries": {}}
    if raw.get("version") != STATE_VERSION or not isinstance(raw.get("entries"), dict):
        return {"version": STATE_VERSION, "entries": {}}
    return raw


def _save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def apply_signal_streaks(
    signals: Iterable[Any],
    *,
    state_path: Path,
    now: datetime,
    action_getter: Callable[[Any], str],
) -> dict[str, Any]:
    """Attach exact-action streaks and persist one idempotent row per symbol.

    A streak advances only on the next closed one-minute candle while action and
    direction are unchanged. Duplicate runs on the same candle do not advance.
    A gap, action change, direction change, WAIT/DATA state, or absent signal
    resets the next tracked state to one.
    """
    state = _load(state_path)
    previous = dict(state.get("entries") or {})
    current_entries: dict[str, dict[str, Any]] = {}
    for signal in signals:
        symbol = str(getattr(signal, "symbol", "")).upper()
        if not symbol:
            continue
        action = str(action_getter(signal) or "")
        direction_value = float(getattr(signal, "direction", 0.0) or 0.0)
        direction = 1 if direction_value > 0 else -1 if direction_value < 0 else 0

        setattr(signal, "action_streak_count", 0)
        setattr(signal, "action_streak_action", "")
        setattr(signal, "action_streak_direction", 0)

        if action not in TRACKED_ACTIONS or direction == 0:
            continue

        minute_ms = _minute_ms(signal, now)
        old = previous.get(symbol) if isinstance(previous.get(symbol), dict) else {}
        old_action = str(old.get("action", ""))
        old_direction = int(old.get("direction", 0) or 0)
        old_minute = int(old.get("minute_ms", 0) or 0)
        old_count = max(0, int(old.get("count", 0) or 0))

        if action == old_action and direction == old_direction:
            delta = minute_ms - old_minute
            if delta == 0 and old_count > 0:
                count = old_count
            elif delta == 60_000 and old_count > 0:
                count = min(MAX_COUNT, old_count + 1)
            else:
                count = 1
        else:
            count = 1

        row = {
            "action": action,
            "direction": direction,
            "minute_ms": minute_ms,
            "count": count,
        }
        current_entries[symbol] = row
        setattr(signal, "action_streak_count", count)
        setattr(signal, "action_streak_action", action)
        setattr(signal, "action_streak_direction", direction)

    # Only current tracked actions survive. This deliberately resets signals
    # that vanished, became invalid, or fell back to WAIT between runs.
    payload = {
        "version": STATE_VERSION,
        "updated_at": now.astimezone(timezone.utc).isoformat(),
        "entries": current_entries,
    }
    _save(state_path, payload)
    return payload


