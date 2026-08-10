# r1
"""Persistent direction-transition protection for CF v7.1.0."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

STATE_VERSION = "signal-transition-v710-r1"
COMPATIBLE_STATE_VERSIONS = {STATE_VERSION, "signal-transition-v700-r1"}
TRACKED_ACTIONS = {"NEAR", "TRY", "NOW"}
STRONG_ACTIONS = {"TRY", "NOW"}
MAX_ENTRY_AGE_MINUTES = 90


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
        return {"version": STATE_VERSION, "entries": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "entries": {}}
    if raw.get("version") not in COMPATIBLE_STATE_VERSIONS or not isinstance(raw.get("entries"), dict):
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


def _set_action(signal: Any, action: str, direction: int) -> None:
    states = {
        ("NEAR", 1): "WATCH_LONG",
        ("NEAR", -1): "WATCH_SHORT",
        ("TRY", 1): "STRONG_LONG",
        ("TRY", -1): "STRONG_SHORT",
        ("NOW", 1): "BUY",
        ("NOW", -1): "SELL",
    }
    state = states.get((action, direction))
    if state:
        setattr(signal, "state", state)


def _consecutive_count(
    *,
    same: bool,
    minute_ms: int,
    old_minute_ms: int,
    old_count: int,
) -> int:
    if not same:
        return 1
    delta = minute_ms - old_minute_ms
    if delta == 0 and old_count > 0:
        return old_count
    if delta == 60_000 and old_count > 0:
        return min(999, old_count + 1)
    return 1


def _reversal_status(signal: Any) -> tuple[bool, bool]:
    if str(getattr(signal, "selected_setup", "")) != "REVERSAL":
        return False, False
    setup = getattr(signal, "reversal", None)
    if setup is None:
        return True, False
    confirmed = bool(
        str(getattr(setup, "phase", "")) == "ready"
        and bool(getattr(setup, "structural_reclaim", False))
        and bool(getattr(setup, "relative_confirmed", True))
        and not bool(getattr(setup, "new_extreme_after_event", False))
        and not bool(getattr(setup, "exit_hint", False))
    )
    return True, confirmed


def apply_signal_transition_guard(
    signals: Iterable[Any],
    *,
    state_path: Path,
    now: datetime,
    config: Mapping[str, Any],
    action_getter: Callable[[Any], str],
) -> dict[str, Any]:
    """Prevent instant opposite TRY/NOW flips while preserving all display rules.

    Rules:
    - An unconfirmed reversal (W?) is always at most NEAR and its effective
      readiness is capped just below TRY.
    - After a recent opposite TRY/NOW, a confirmed W needs two consecutive
      confirmed closes for TRY and three for NOW.
    - Other opposite setups need two consecutive directional closes for TRY
      and three for NOW.
    - Duplicate runs on the same candle are idempotent.
    """
    state = _load(state_path)
    previous = dict(state.get("entries") or {})
    current_entries: dict[str, dict[str, Any]] = {}

    guard_minutes = max(1, int(config.get("signal_flip_guard_minutes", 10)))
    try_minutes = max(2, int(config.get("signal_flip_try_confirmed_minutes", 2)))
    now_minutes = max(try_minutes + 1, int(config.get("signal_flip_now_confirmed_minutes", 3)))
    strong_threshold = float(config.get("strong_trade_readiness", 61.0))
    unconfirmed_cap = min(
        strong_threshold - 1.0,
        float(config.get("unconfirmed_reversal_max_readiness", strong_threshold - 1.0)),
    )

    guarded = 0
    capped = 0
    for signal in signals:
        symbol = str(getattr(signal, "symbol", "")).upper()
        if not symbol:
            continue

        setattr(signal, "transition_guard_active", False)
        setattr(signal, "transition_guard_from_direction", 0)
        setattr(signal, "transition_guard_direction_streak", 0)
        setattr(signal, "transition_guard_confirmed_streak", 0)
        setattr(signal, "transition_guard_reason", "")

        minute_ms = _minute_ms(signal, now)
        raw_action = str(action_getter(signal) or "")
        direction = _direction(signal)
        old = previous.get(symbol) if isinstance(previous.get(symbol), dict) else {}

        old_candidate_direction = int(old.get("candidate_direction", 0) or 0)
        old_candidate_minute = int(old.get("candidate_minute_ms", 0) or 0)
        old_direction_streak = int(old.get("direction_streak", 0) or 0)
        tracked_now = raw_action in TRACKED_ACTIONS and direction != 0
        direction_streak = 0
        if tracked_now:
            direction_streak = _consecutive_count(
                same=direction == old_candidate_direction,
                minute_ms=minute_ms,
                old_minute_ms=old_candidate_minute,
                old_count=old_direction_streak,
            )

        is_reversal, reversal_confirmed = _reversal_status(signal)
        old_confirmed_direction = int(old.get("confirmed_reversal_direction", 0) or 0)
        old_confirmed_minute = int(old.get("confirmed_reversal_minute_ms", 0) or 0)
        old_confirmed_streak = int(old.get("confirmed_reversal_streak", 0) or 0)
        confirmed_streak = 0
        if tracked_now and is_reversal and reversal_confirmed:
            confirmed_streak = _consecutive_count(
                same=direction == old_confirmed_direction,
                minute_ms=minute_ms,
                old_minute_ms=old_confirmed_minute,
                old_count=old_confirmed_streak,
            )

        last_strong_direction = int(old.get("last_strong_direction", 0) or 0)
        last_strong_minute = int(old.get("last_strong_minute_ms", 0) or 0)
        last_strong_action = str(old.get("last_strong_action", "") or "")
        age_ms = minute_ms - last_strong_minute
        recent_opposite_strong = bool(
            tracked_now
            and last_strong_direction in {-1, 1}
            and direction == -last_strong_direction
            and 0 <= age_ms <= guard_minutes * 60_000
        )

        reasons: list[str] = []

        # W? may be interesting, but it is not a TRY/NOW permission.
        if tracked_now and is_reversal and not reversal_confirmed:
            if raw_action in STRONG_ACTIONS:
                _set_action(signal, "NEAR", direction)
                guarded += 1
            current_readiness = float(getattr(signal, "trade_readiness", 0.0) or 0.0)
            if current_readiness > unconfirmed_cap:
                setattr(signal, "trade_readiness", max(0.0, unconfirmed_cap))
                capped += 1
            reasons.append("W? bleibt bis zum strukturellen Reclaim NEAR")

        # A direct direction flip needs persistence, not just one fresh candle.
        effective_action = str(action_getter(signal) or "")
        if recent_opposite_strong and effective_action in TRACKED_ACTIONS:
            setattr(signal, "transition_guard_active", True)
            setattr(signal, "transition_guard_from_direction", last_strong_direction)
            required_streak = confirmed_streak if is_reversal else direction_streak
            setattr(signal, "transition_guard_direction_streak", direction_streak)
            setattr(signal, "transition_guard_confirmed_streak", confirmed_streak)

            if is_reversal and not reversal_confirmed:
                # Already limited to NEAR above.
                reasons.append(f"Gegenrichtung nach {last_strong_action or 'TRY/NOW'} wartet auf bestätigtes W")
            elif effective_action == "NOW" and required_streak < now_minutes:
                _set_action(
                    signal,
                    "TRY" if required_streak >= try_minutes else "NEAR",
                    direction,
                )
                guarded += 1
                reasons.append(
                    f"Gegenrichtung braucht {now_minutes} bestätigte Minuten für NOW"
                )
            elif effective_action == "TRY" and required_streak < try_minutes:
                _set_action(signal, "NEAR", direction)
                guarded += 1
                reasons.append(
                    f"Gegenrichtung braucht {try_minutes} bestätigte Minuten für TRY"
                )

        final_action = str(action_getter(signal) or "")
        # During an opposite transition, TRY is deliberately provisional.
        # Keep the previous strong direction as the active guard until the new
        # direction reaches NOW (or the guard naturally expires). This also
        # preserves TRY persistence without changing the visible token.
        promote_strong_reference = bool(
            final_action == "NOW"
            or (final_action == "TRY" and not recent_opposite_strong)
        )
        if promote_strong_reference and direction != 0:
            last_strong_direction = direction
            last_strong_minute = minute_ms
            last_strong_action = final_action

        if reasons:
            existing = getattr(signal, "reasons", None)
            if isinstance(existing, list):
                existing.extend(reason for reason in reasons if reason not in existing)
            setattr(signal, "transition_guard_reason", "; ".join(reasons))

        # Retain recent strong context across WAIT/NO_TRADE runs, but prune old rows.
        keep_strong = bool(
            last_strong_direction in {-1, 1}
            and 0 <= minute_ms - last_strong_minute <= MAX_ENTRY_AGE_MINUTES * 60_000
        )
        if tracked_now or keep_strong:
            current_entries[symbol] = {
                "candidate_direction": direction if tracked_now else 0,
                "candidate_minute_ms": minute_ms,
                "direction_streak": direction_streak,
                "confirmed_reversal_direction": direction if confirmed_streak else 0,
                "confirmed_reversal_minute_ms": minute_ms if confirmed_streak else 0,
                "confirmed_reversal_streak": confirmed_streak,
                "last_strong_direction": last_strong_direction if keep_strong or final_action in STRONG_ACTIONS else 0,
                "last_strong_minute_ms": last_strong_minute if keep_strong or final_action in STRONG_ACTIONS else 0,
                "last_strong_action": last_strong_action if keep_strong or final_action in STRONG_ACTIONS else "",
            }

    payload = {
        "version": STATE_VERSION,
        "updated_at": now.astimezone(timezone.utc).isoformat(),
        "guarded": guarded,
        "readiness_capped": capped,
        "entries": current_entries,
    }
    _save(state_path, payload)
    return payload


