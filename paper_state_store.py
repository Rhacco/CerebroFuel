"""Restore and checkpoint the v3.8.1 paper state through the GitHub API."""
from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


API = "https://api.github.com"
BRANCH = "paper-state"
REMOTE_FILE = "paper_state.json"
APP_VERSION = "3.8.1"
COMPATIBLE_APP_VERSIONS = {"3.7", "3.7.1", "3.8.0", APP_VERSION}


class GitHubStateStore:
    def __init__(self) -> None:
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        self.repository = os.getenv("GITHUB_REPOSITORY", "").strip()
        self.source_sha = os.getenv("GITHUB_SHA", "").strip()

    @property
    def available(self) -> bool:
        return bool(self.token and self.repository)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        data = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "cf-paper-state/3.8.1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if allow_missing and exc.code == 404:
                return None
            body = exc.read(500).decode("utf-8", "replace")
            raise RuntimeError(
                f"GitHub-State-Aufruf fehlgeschlagen ({exc.code}): {body}"
            ) from exc

    def remote_file(self) -> dict[str, Any] | None:
        return self.request(
            "GET",
            f"/repos/{self.repository}/contents/{REMOTE_FILE}?ref={quote(BRANCH)}",
            allow_missing=True,
        )

    def ensure_branch(self) -> None:
        current = self.request(
            "GET",
            f"/repos/{self.repository}/git/ref/heads/{quote(BRANCH)}",
            allow_missing=True,
        )
        if current is not None:
            return
        if not self.source_sha:
            raise RuntimeError("GITHUB_SHA fehlt zum Erstellen des State-Branches")
        self.request(
            "POST",
            f"/repos/{self.repository}/git/refs",
            {"ref": f"refs/heads/{BRANCH}", "sha": self.source_sha},
        )


def _read_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Paper-State ist nicht lesbar: {exc}") from exc
    if (
        not isinstance(state, dict)
        or state.get("app_version") not in COMPATIBLE_APP_VERSIONS
        or int(state.get("schema", -1)) != 1
    ):
        raise RuntimeError("Paper-State ist inkompatibel")
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".store.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def restore(path: Path, store: GitHubStateStore) -> int:
    if path.exists():
        _read_state(path)
        print("Paper-State aus dem Laufzeit-Cache geladen.")
        return 0
    if not store.available:
        print("Kein GitHub-State konfiguriert; neuer lokaler Paper-Stand.")
        return 0
    remote = store.remote_file()
    if remote is None:
        print("Noch kein Paper-Checkpoint vorhanden.")
        return 0
    try:
        content = base64.b64decode(str(remote["content"])).decode("utf-8")
        state = json.loads(content)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Remote Paper-State ist beschädigt: {exc}") from exc
    _write_state(path, state)
    _read_state(path)
    print("Paper-State aus dem dauerhaften Checkpoint geladen.")
    return 0


def checkpoint(
    path: Path,
    store: GitHubStateStore,
    interval_hours: float,
    force: bool,
) -> int:
    state = _read_state(path)
    if not store.available:
        print("Kein GitHub-State konfiguriert; Checkpoint lokal belassen.")
        return 0
    now = datetime.now(timezone.utc)
    previous_raw = state.get("last_checkpoint_at")
    previous: datetime | None = None
    if previous_raw:
        try:
            previous = datetime.fromisoformat(str(previous_raw))
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
        except ValueError:
            previous = None
    due = (
        force
        or bool(state.get("checkpoint_requested"))
        or previous is None
        or (now - previous).total_seconds() >= interval_hours * 3600
    )
    if not due:
        print("Paper-Checkpoint ist noch aktuell.")
        return 0

    store.ensure_branch()
    current = store.remote_file()
    uploaded_state = dict(state)
    uploaded_state["last_checkpoint_at"] = now.isoformat()
    uploaded_state["checkpoint_requested"] = False
    encoded = base64.b64encode(
        (
            json.dumps(
                uploaded_state,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ).decode("ascii")
    payload: dict[str, Any] = {
        "message": f"paper checkpoint {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "content": encoded,
        "branch": BRANCH,
    }
    if current is not None:
        payload["sha"] = str(current["sha"])
    store.request(
        "PUT",
        f"/repos/{store.repository}/contents/{REMOTE_FILE}",
        payload,
    )
    _write_state(path, uploaded_state)
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
    return checkpoint(
        args.path,
        store,
        max(0.25, args.interval_hours),
        args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())

