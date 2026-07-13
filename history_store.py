"""Persistent history cache helpers for ephemeral GitHub runners."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis import PricePoint, normalize_history

SCHEMA_VERSION = 2


def _downsample_hourly(points: list[PricePoint], cutoff_ms: int) -> list[PricePoint]:
    by_hour: dict[int, PricePoint] = {}
    hour_ms = 3_600_000
    for point in points:
        if point.timestamp_ms < cutoff_ms:
            continue
        bucket = point.timestamp_ms // hour_ms
        previous = by_hour.get(bucket)
        if previous is None or point.timestamp_ms > previous.timestamp_ms:
            by_hour[bucket] = point
    return sorted(by_hour.values(), key=lambda point: point.timestamp_ms)


def load_cache(path: Path) -> tuple[datetime | None, dict[str, list[PricePoint]]]:
    if not path.exists():
        return None, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            return None, {}
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        histories = {
            str(code).upper(): normalize_history(rows)
            for code, rows in (data.get("histories") or {}).items()
            if isinstance(rows, list)
        }
        return fetched_at, histories
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, {}


def save_cache(
    path: Path,
    *,
    fetched_at: datetime,
    histories: dict[str, list[PricePoint]],
    history_days: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cutoff_ms = int((fetched_at.timestamp() - history_days * 86_400) * 1000)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(),
        "histories": {},
    }
    for code, points in histories.items():
        compact = _downsample_hourly(points, cutoff_ms)
        payload["histories"][code] = [
            {
                "date": point.timestamp_ms,
                "rate": point.rate,
                "volume": point.volume,
            }
            for point in compact
        ]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
