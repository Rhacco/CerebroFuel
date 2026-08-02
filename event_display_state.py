"""Hourly display throttling for future events in CF v4.0.0.

Future-day events are shown once in the first successful Discord report of each
local clock hour. Events scheduled for today, active incidents, and date-only
events on their verified day remain visible in every report until they occur.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

STATE_VERSION = "event-display-v400-r1"


@dataclass(frozen=True)
class EventDisplayPlan:
    codes: dict[str, str]
    due_keys: tuple[str, ...]
    hour_key: str


def _value(mark: Any, name: str, default: Any = None) -> Any:
    if isinstance(mark, Mapping):
        return mark.get(name, default)
    return getattr(mark, name, default)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "sent": {}}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "sent": {}}
    sent = raw.get("sent")
    if not isinstance(sent, dict):
        raw["sent"] = {}
    return raw


def _save(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def _fingerprint(symbol: str, mark: Any) -> str:
    return "|".join(
        (
            symbol,
            str(_value(mark, "kind", "")),
            str(_value(mark, "starts_at", "")),
            str(_value(mark, "title", "")),
        )
    )


def plan_event_display(
    *,
    marks: Mapping[str, Any],
    now: datetime,
    timezone_name: str,
    state_path: Path,
) -> EventDisplayPlan:
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    hour_key = local_now.strftime("%Y-%m-%dT%H")
    today = local_now.date()
    state = _load(state_path)
    sent = {
        str(key): str(value)
        for key, value in (state.get("sent") or {}).items()
        if isinstance(key, str) and isinstance(value, str)
    }

    codes: dict[str, str] = {}
    due: list[str] = []
    for symbol, mark in marks.items():
        code = str(_value(mark, "code", "") or "")
        if not code:
            continue
        starts = _parse_iso(_value(mark, "starts_at"))
        active = bool(_value(mark, "active", False))
        is_today = starts is not None and starts.astimezone(zone).date() <= today
        if active or is_today or starts is None:
            codes[str(symbol)] = code
            continue

        fingerprint = _fingerprint(str(symbol), mark)
        if sent.get(fingerprint) != hour_key:
            codes[str(symbol)] = code
            due.append(fingerprint)
    return EventDisplayPlan(
        codes=codes,
        due_keys=tuple(sorted(set(due))),
        hour_key=hour_key,
    )


def mark_event_displayed(
    *,
    state_path: Path,
    plan: EventDisplayPlan,
    now: datetime,
) -> None:
    if not plan.due_keys:
        return
    state = _load(state_path)
    sent = dict(state.get("sent") or {})
    for key in plan.due_keys:
        sent[key] = plan.hour_key
    # Keep only the latest 256 event fingerprints. Old entries are harmless,
    # but bounding the file avoids indefinite growth.
    if len(sent) > 256:
        sent = dict(list(sent.items())[-256:])
    _save(
        state_path,
        {
            "version": STATE_VERSION,
            "updated_at": now.isoformat(),
            "sent": sent,
        },
    )
