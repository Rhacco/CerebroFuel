"""Discord change detection and heartbeat state for v3.5.

A send decision never marks a report as delivered.  The state is committed only
after Discord accepts the message, so a network failure is retried next run.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

STATE_VERSION = "notification-v350-r1"


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": STATE_VERSION}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION}
    return raw


def _save(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def report_send_decision(
    *,
    path: Path,
    report: str,
    now_ms: int,
    config: Mapping[str, Any],
    force: bool = False,
) -> tuple[bool, str, str]:
    section = config.get("notifications") if isinstance(config, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    heartbeat_minutes = max(3, int(section.get("heartbeat_minutes", 15)))
    state = _load(path)
    # The header minute changes every run; exclude it from semantic change
    # detection so the 3-minute calculator does not spam Discord.
    lines = report.splitlines()
    if lines:
        lines[0] = re.sub(r":\d{2}$", ":--", lines[0])
    semantic_report = "\n".join(lines)
    digest = hashlib.sha256(semantic_report.encode("utf-8")).hexdigest()
    previous_digest = str(state.get("digest") or "")
    last_sent = int(state.get("last_sent_ms") or 0)
    heartbeat_due = now_ms - last_sent >= heartbeat_minutes * 60_000
    changed = digest != previous_digest
    send = bool(force or changed or heartbeat_due or last_sent <= 0)
    reason = "forced" if force else ("changed" if changed else ("heartbeat" if heartbeat_due else "unchanged"))
    return send, reason, digest


def mark_report_sent(*, path: Path, digest: str, now_ms: int, reason: str) -> None:
    _save(path, {
        "version": STATE_VERSION,
        "digest": str(digest),
        "last_sent_ms": int(now_ms),
        "last_reason": str(reason),
    })
# Package revision: v3.5.0-buy-gate-fix-r5

# Package revision: v3.5.0-buy-gate-fix-r5
