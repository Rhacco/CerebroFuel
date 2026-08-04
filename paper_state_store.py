# Package revision: r1
"""Strict restore/checkpoint store for the independent v5.2.0 paper state."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.github.com"
BRANCH = "paper-state-v520"
REMOTE_FILE = "paper_state_v520.json"
APP_VERSION = "5.2.0"
STATE_SCHEMA = 1


class GitHubStateStore:
    def __init__(self) -> None:
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        self.repository = os.getenv("GITHUB_REPOSITORY", "").strip()
        self.source_sha = os.getenv("GITHUB_SHA", "").strip()

    @property
    def available(self) -> bool:
        return bool(self.token and self.repository)

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, allow_missing: bool = False) -> dict[str, Any] | None:
        data = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        request = Request(API + path, data=data, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "cf-paper-state/5.2.0",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if allow_missing and exc.code == 404:
                return None
            body = exc.read(500).decode("utf-8", "replace")
            raise RuntimeError(f"GitHub-State-Aufruf fehlgeschlagen ({exc.code}): {body}") from exc

    def remote_file(self) -> dict[str, Any] | None:
        return self.request("GET", f"/repos/{self.repository}/contents/{REMOTE_FILE}?ref={quote(BRANCH)}", allow_missing=True)

    def ensure_branch(self) -> None:
        current = self.request("GET", f"/repos/{self.repository}/git/ref/heads/{quote(BRANCH)}", allow_missing=True)
        if current is not None:
            return
        if not self.source_sha:
            raise RuntimeError("GITHUB_SHA fehlt zum Erstellen des State-Branches")
        self.request("POST", f"/repos/{self.repository}/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": self.source_sha})


def _validate_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("Paper-State ist kein JSON-Objekt")
    try:
        schema = int(raw.get("schema", -1))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Paper-State-Schema ist ungültig") from exc
    if schema != STATE_SCHEMA:
        raise RuntimeError("Paper-State-Schema ist inkompatibel")
    if raw.get("app_version") != APP_VERSION:
        raise RuntimeError("Paper-State gehört nicht zu v5.2.0")
    if not isinstance(raw.get("positions"), dict):
        raise RuntimeError("Paper-State-Positionen sind ungültig")
    try:
        balance = float(raw.get("balance_usd", -1.0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Paper-State-Kontostand ist ungültig") from exc
    if balance < 0:
        raise RuntimeError("Paper-State-Kontostand ist negativ")
    return dict(raw)


def _read_state(path: Path) -> dict[str, Any]:
    try:
        return _validate_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Paper-State ist nicht lesbar: {exc}") from exc


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".store.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def restore(path: Path, store: GitHubStateStore) -> int:
    if path.exists():
        try:
            _read_state(path)
        except RuntimeError as exc:
            path.unlink(missing_ok=True)
            print(f"Warnung: unbrauchbarer v5.2.0-Paper-State wurde verworfen ({exc}).", file=sys.stderr)
        else:
            print("Paper-State aus dem Laufzeit-Cache geladen.")
            return 0
    if not store.available:
        print("Kein GitHub-State konfiguriert; neuer lokaler Paper-Stand.")
        return 0
    remote = store.remote_file()
    if remote is None:
        print("Noch kein v5.2.0-Paper-Checkpoint vorhanden.")
        return 0
    try:
        state = _validate_state(json.loads(base64.b64decode(str(remote["content"])).decode("utf-8")))
    except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Warnung: Remote Paper-State ist nicht nutzbar; neuer Stand wird aufgebaut ({exc}).", file=sys.stderr)
        return 0
    _write_state(path, state)
    print("Paper-State aus dem dauerhaften Checkpoint geladen.")
    return 0


def checkpoint(path: Path, store: GitHubStateStore, interval_hours: float, force: bool) -> int:
    state = _read_state(path)
    if not store.available:
        print("Kein GitHub-State konfiguriert; Checkpoint lokal belassen.")
        return 0
    now = datetime.now(timezone.utc)
    previous = None
    if state.get("last_checkpoint_at"):
        try:
            previous = datetime.fromisoformat(str(state["last_checkpoint_at"]))
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
        except ValueError:
            previous = None
    due = force or bool(state.get("checkpoint_requested")) or previous is None or (now - previous).total_seconds() >= interval_hours * 3600
    if not due:
        print("Paper-Checkpoint ist noch aktuell.")
        return 0
    store.ensure_branch()
    current = store.remote_file()
    uploaded = dict(state)
    uploaded["last_checkpoint_at"] = now.isoformat()
    uploaded["checkpoint_requested"] = False
    encoded = base64.b64encode((json.dumps(uploaded, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()).decode("ascii")
    payload: dict[str, Any] = {"message": f"paper checkpoint {now.strftime('%Y-%m-%d %H:%M UTC')}", "content": encoded, "branch": BRANCH}
    if current is not None:
        payload["sha"] = str(current["sha"])
    store.request("PUT", f"/repos/{store.repository}/contents/{REMOTE_FILE}", payload)
    _write_state(path, uploaded)
    print("Paper-State dauerhaft gespeichert.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("restore", "checkpoint"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--interval-hours", type=float, default=6.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    store = GitHubStateStore()
    if args.operation == "restore":
        return restore(args.path, store)
    return checkpoint(args.path, store, max(0.25, args.interval_hours), args.force)


if __name__ == "__main__":
    raise SystemExit(main())
