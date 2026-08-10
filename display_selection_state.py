# r4
"""Small persistent state for stable Discord radar/detail membership."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

STATE_VERSION = "display-selection-v700-r1"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_display_selection_state(
    path: Path | None,
    *,
    now: datetime,
    allowed_symbols: set[str],
) -> dict[str, Any]:
    blank = {"radar": [], "detail": [], "detail_last_qualified": {}}
    if path is None or not path.exists():
        return blank
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return blank
    if payload.get("version") != STATE_VERSION:
        return blank
    updated = _parse_time(payload.get("updated_at"))
    if updated is None or now.astimezone(timezone.utc) - updated.astimezone(timezone.utc) > timedelta(hours=6):
        return blank

    radar = [
        str(value).upper() for value in payload.get("radar", [])
        if str(value).upper() in allowed_symbols
    ][:3]
    detail = [
        str(value).upper() for value in payload.get("detail", [])
        if str(value).upper() in allowed_symbols
    ][:4]
    raw_last = payload.get("detail_last_qualified")
    raw_last = raw_last if isinstance(raw_last, Mapping) else {}
    last: dict[str, str] = {}
    for symbol, value in raw_last.items():
        symbol = str(symbol).upper()
        stamp = _parse_time(value)
        if symbol not in allowed_symbols or stamp is None:
            continue
        if now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc) <= timedelta(hours=1):
            last[symbol] = stamp.astimezone(timezone.utc).isoformat()
    return {"radar": radar, "detail": detail, "detail_last_qualified": last}


def save_display_selection_state(
    path: Path | None,
    *,
    now: datetime,
    radar_symbols: list[str],
    detail_symbols: list[str],
    detail_last_qualified: Mapping[str, str],
) -> None:
    if path is None:
        return
    payload = {
        "version": STATE_VERSION,
        "updated_at": now.astimezone(timezone.utc).isoformat(),
        "radar": list(dict.fromkeys(str(value).upper() for value in radar_symbols))[:3],
        "detail": list(dict.fromkeys(str(value).upper() for value in detail_symbols))[:4],
        "detail_last_qualified": {
            str(symbol).upper(): str(value)
            for symbol, value in detail_last_qualified.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)
