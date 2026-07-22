"""Dynamic, bounded unlock-risk deductions for v3.3.2 ranking."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping


def _event_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def unlock_context(display: str, config: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    section = config.get("unlock_risk") if isinstance(config, Mapping) else None
    if not isinstance(section, Mapping) or not bool(section.get("enabled", True)):
        return {"penalty": 0.0, "risk": "none", "event_date": None, "days_to_event": None, "recipient": None}
    events = section.get("events")
    item = events.get(str(display).upper()) if isinstance(events, Mapping) else None
    if not isinstance(item, Mapping):
        return {"penalty": 0.0, "risk": "none", "event_date": None, "days_to_event": None, "recipient": None}

    current = (now or datetime.now(timezone.utc)).date()
    event = _event_date(item.get("date"))
    base = max(0.0, float(item.get("base_penalty", 0.0)))
    structural = max(0.0, float(item.get("structural_penalty", 0.0)))
    event_component = 0.0
    days: int | None = None
    if event is not None:
        days = (event - current).days
        if days >= 0:
            if days <= 3:
                factor = 1.00
            elif days <= 14:
                factor = 1.00 - (days - 3) * (0.20 / 11.0)
            elif days <= 45:
                factor = 0.80 - (days - 14) * (0.42 / 31.0)
            elif days <= 120:
                factor = 0.38 - (days - 45) * (0.30 / 75.0)
            else:
                factor = 0.0
        else:
            age = -days
            factor = 0.70 if age <= 3 else (0.42 if age <= 7 else (0.20 if age <= 21 else 0.0))
        event_component = base * max(0.0, factor)
    maximum = max(0.0, float(section.get("maximum_penalty", 20.0)))
    penalty = min(maximum, max(structural, event_component))
    return {
        "penalty": round(penalty, 4),
        "risk": str(item.get("risk") or "medium"),
        "event_date": event.isoformat() if event else None,
        "days_to_event": days,
        "recipient": item.get("recipient"),
        "source": item.get("source"),
    }
