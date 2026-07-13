"""Read short-term LCW snapshots collected by the Cloudflare scheduler."""

from __future__ import annotations

from typing import Any

import requests


class StateReadError(RuntimeError):
    pass


def load_snapshots(url: str, key: str, timeout: int = 20) -> list[dict[str, Any]]:
    if not url:
        return []
    headers = {"accept": "application/json", "cache-control": "no-cache"}
    if key:
        headers["x-state-key"] = key
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise StateReadError(f"Cloudflare-Zeitdaten konnten nicht geladen werden: {exc}") from exc
    if not isinstance(data, dict):
        raise StateReadError("Cloudflare-Zeitdaten haben ein ungültiges Format.")
    snapshots = data.get("snapshots", [])
    if not isinstance(snapshots, list):
        raise StateReadError("Cloudflare-Zeitdaten enthalten keine Snapshot-Liste.")
    valid = [snapshot for snapshot in snapshots if isinstance(snapshot, dict)]
    valid.sort(key=lambda item: int(item.get("ts", 0)))
    return valid
