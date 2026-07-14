"""Reliable daily seasonality state for crypto-signal-monitor v3.2.6."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from analysis import (
    DAY_NAMES,
    DISPLAY_WEEK_ORDER,
    PricePoint,
    Seasonality,
    analyze_seasonality,
    rolling_week_returns,
)

STATE_VERSION = "3.2.6"
STATE_REVISION = "daily-retry-cache-r4"


class InsufficientDailyHistory(RuntimeError):
    """Raised when LCW returned data but not enough completed days to evaluate."""


def local_day_key(now: datetime, timezone: str) -> str:
    return now.astimezone(ZoneInfo(timezone)).date().isoformat()


def seasonality_from_dict(raw: Mapping[str, Any] | None) -> Seasonality:
    item = raw or {}
    return Seasonality(
        current=str(item.get("current", "?")),
        best_weekdays=tuple(str(value) for value in item.get("best_weekdays", []) if str(value)),
        samples=int(item.get("samples", 0)),
        source=str(item.get("source", "daily-cache-missing")),
        current_score=(float(item["current_score"]) if item.get("current_score") is not None else None),
        current_confidence=float(item.get("current_confidence", 0.0)),
        weekday_scores={str(key): float(value) for key, value in (item.get("weekday_scores") or {}).items()},
        weekday_confidence={
            str(key): float(value) for key, value in (item.get("weekday_confidence") or {}).items()
        },
    )


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def state_fingerprint(state: Mapping[str, Any]) -> str:
    """Stable representation used to decide whether a new immutable GH cache is needed."""
    relevant = {
        "version": state.get("version"),
        "revision": state.get("revision"),
        "date": state.get("date"),
        "coins": state.get("coins", {}),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _streaks(raw: Mapping[str, Any] | None, field: str) -> dict[str, int]:
    source = (raw or {}).get(field) or {}
    return {name: max(0, int(source.get(name, 0))) for name in DAY_NAMES}


def _candidate_days(
    raw: Seasonality,
    *,
    bootstrap: bool,
    bootstrap_min_score: float,
    bootstrap_min_confidence: float,
) -> list[str]:
    """Return robust raw candidates; bootstrap may use the strongest positive fallback.

    `analyze_seasonality` intentionally uses strict entry rules. On the first valid
    state we additionally allow its strongest independently positive score, but
    only with enough confidence. This prevents an all-empty initial cache without
    forcing a weak or negative weekday into the report.
    """
    candidates = set(raw.best_weekdays)
    if bootstrap:
        eligible = [
            day
            for day in DAY_NAMES
            if raw.weekday_scores.get(day, float("-inf")) >= bootstrap_min_score
            and raw.weekday_confidence.get(day, 0.0) >= bootstrap_min_confidence
        ]
        eligible.sort(
            key=lambda day: (
                raw.weekday_scores.get(day, -999.0),
                raw.weekday_confidence.get(day, 0.0),
            ),
            reverse=True,
        )
        if eligible:
            candidates.add(eligible[0])
    return sorted(
        candidates,
        key=lambda day: (
            raw.weekday_scores.get(day, -999.0),
            raw.weekday_confidence.get(day, 0.0),
        ),
        reverse=True,
    )


def _stable_days(
    raw: Seasonality,
    previous: Mapping[str, Any] | None,
    *,
    enter_days: int,
    exit_days: int,
    bootstrap_min_samples: int = 300,
    bootstrap_min_score: float = 0.012,
    bootstrap_min_confidence: float = 0.50,
    bootstrap_second_min_score: float = 0.035,
    bootstrap_second_min_confidence: float = 0.58,
) -> tuple[tuple[str, ...], dict[str, int], dict[str, int], bool, str]:
    """Apply daily hysteresis with a conservative, useful first initialization."""
    previous = previous or {}
    previous_days = tuple(str(value) for value in previous.get("stable_best_weekdays", []))
    enter = _streaks(previous, "enter_streaks")
    exit_ = _streaks(previous, "exit_streaks")
    was_initialized = bool(previous.get("weekday_initialized", False))
    bootstrap = not was_initialized

    ranked = _candidate_days(
        raw,
        bootstrap=bootstrap and raw.samples >= bootstrap_min_samples,
        bootstrap_min_score=bootstrap_min_score,
        bootstrap_min_confidence=bootstrap_min_confidence,
    )
    raw_days = set(ranked)

    for day in DAY_NAMES:
        if day in raw_days:
            enter[day] = enter.get(day, 0) + 1
            exit_[day] = 0
        else:
            enter[day] = 0
            exit_[day] = exit_.get(day, 0) + 1

    selected: list[str] = []
    for day in previous_days:
        if day in DAY_NAMES and exit_.get(day, 0) < exit_days:
            selected.append(day)

    immediate_count = 0
    for index, day in enumerate(ranked):
        if day in selected:
            continue
        score = raw.weekday_scores.get(day, 0.0)
        confidence = raw.weekday_confidence.get(day, 0.0)
        immediate = False
        if bootstrap and raw.samples >= bootstrap_min_samples:
            if index == 0:
                immediate = score >= bootstrap_min_score and confidence >= bootstrap_min_confidence
            elif index == 1 and ranked:
                top_score = raw.weekday_scores.get(ranked[0], 0.0)
                immediate = (
                    score >= bootstrap_second_min_score
                    and confidence >= bootstrap_second_min_confidence
                    and score >= top_score * 0.72
                )
        if enter.get(day, 0) >= enter_days or immediate:
            selected.append(day)
            immediate_count += int(immediate)

    previous_position = {day: index for index, day in enumerate(previous_days)}
    selected = sorted(
        dict.fromkeys(selected),
        key=lambda day: (
            raw.weekday_scores.get(day, float((previous.get("weekday_scores") or {}).get(day, -999.0))),
            raw.weekday_confidence.get(
                day, float((previous.get("weekday_confidence") or {}).get(day, 0.0))
            ),
            -previous_position.get(day, 99),
        ),
        reverse=True,
    )[:2]
    selected.sort(key=lambda day: DISPLAY_WEEK_ORDER.index(DAY_NAMES.index(day)))

    initialized = was_initialized or bool(selected)
    if immediate_count:
        mode = "bootstrap-immediate"
    elif was_initialized:
        mode = "daily-hysteresis"
    else:
        mode = "bootstrap-no-qualified-day"
    return tuple(selected), enter, exit_, initialized, mode


def _weekday_diagnostics(raw: Seasonality, stable_days: Sequence[str], mode: str) -> dict[str, Any]:
    ranked = sorted(
        raw.weekday_scores,
        key=lambda day: (raw.weekday_scores.get(day, -999.0), raw.weekday_confidence.get(day, 0.0)),
        reverse=True,
    )
    return {
        "mode": mode,
        "samples": raw.samples,
        "source": raw.source,
        "raw": list(raw.best_weekdays),
        "stable": list(stable_days),
        "top": [
            {
                "day": day,
                "score": round(float(raw.weekday_scores.get(day, 0.0)), 5),
                "confidence": round(float(raw.weekday_confidence.get(day, 0.0)), 5),
                "qualified": day in raw.best_weekdays,
            }
            for day in ranked[:4]
        ],
    }


def build_daily_coin_context(
    *,
    display: str,
    api_code: str,
    history: Sequence[PricePoint],
    now: datetime,
    timezone: str,
    config: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    computed_for: str,
    attempt_count: int = 1,
) -> dict[str, Any]:
    minimum_observations = int(config.get("seasonality_min_observations", 180))
    raw = analyze_seasonality(
        list(history),
        now,
        timezone,
        block_hours=int(config.get("time_block_hours", 4)),
        min_samples=int(config.get("seasonality_min_samples", 24)),
        minimum_observations=minimum_observations,
        lookback_days=int(config.get("seasonality_lookback_days", 365)),
    )
    if raw.source == "completed-days-insufficient" or raw.samples < minimum_observations:
        raise InsufficientDailyHistory(
            f"nur {raw.samples}/{minimum_observations} abgeschlossene Tage auswertbar"
        )

    stable_days, enter, exit_, initialized, mode = _stable_days(
        raw,
        previous,
        enter_days=int(config.get("weekday_enter_confirmations", 2)),
        exit_days=int(config.get("weekday_exit_confirmations", 2)),
        bootstrap_min_samples=int(config.get("weekday_bootstrap_min_samples", 300)),
        bootstrap_min_score=float(config.get("weekday_bootstrap_min_score", 0.012)),
        bootstrap_min_confidence=float(config.get("weekday_bootstrap_min_confidence", 0.50)),
        bootstrap_second_min_score=float(config.get("weekday_bootstrap_second_min_score", 0.035)),
        bootstrap_second_min_confidence=float(config.get("weekday_bootstrap_second_min_confidence", 0.58)),
    )
    stable = Seasonality(
        current=raw.current,
        best_weekdays=stable_days,
        samples=raw.samples,
        source=f"daily-completed-days-{mode}",
        current_score=raw.current_score,
        current_confidence=raw.current_confidence,
        weekday_scores=raw.weekday_scores,
        weekday_confidence=raw.weekday_confidence,
    )
    returns = rolling_week_returns(list(history))
    return {
        "display": display,
        "api_code": api_code,
        "seasonality": asdict(stable),
        "raw_best_weekdays": list(raw.best_weekdays),
        "stable_best_weekdays": list(stable_days),
        "weekday_initialized": initialized,
        "enter_streaks": enter,
        "exit_streaks": exit_,
        "weekday_scores": raw.weekday_scores,
        "weekday_confidence": raw.weekday_confidence,
        "weekday_diagnostics": _weekday_diagnostics(raw, stable_days, mode),
        "week_returns": [round(float(value), 8) for value in returns[-420:]],
        "history_points": len(history),
        "status": "complete",
        "computed_for": computed_for,
        "attempt_count": attempt_count,
        "last_error": None,
        "last_attempt_at": now.isoformat(),
    }


def carry_forward_context(
    *,
    display: str,
    api_code: str,
    previous: Mapping[str, Any] | None,
    computed_for: str,
    now: datetime,
    reason: str,
    attempt_count: int,
) -> dict[str, Any]:
    """Keep the last valid weekdays visible, but mark the current day for retry."""
    if previous:
        carried = dict(previous)
        carried["display"] = display
        carried["api_code"] = api_code
        carried["status"] = "stale-retry"
        carried["target_date"] = computed_for
        carried["attempt_count"] = attempt_count
        carried["last_error"] = reason
        carried["last_attempt_at"] = now.isoformat()
        seasonality = dict(carried.get("seasonality") or {})
        # The historical weekday list remains useful; the current-day score does not.
        seasonality.update({"current": "?", "current_score": None, "current_confidence": 0.0})
        carried["seasonality"] = seasonality
        diagnostics = dict(carried.get("weekday_diagnostics") or {})
        diagnostics["mode"] = "stale-retry"
        diagnostics["error"] = reason
        carried["weekday_diagnostics"] = diagnostics
        return carried
    return {
        "display": display,
        "api_code": api_code,
        "seasonality": asdict(Seasonality("?", tuple(), 0, "daily-cache-unavailable")),
        "raw_best_weekdays": [],
        "stable_best_weekdays": [],
        "weekday_initialized": False,
        "enter_streaks": {day: 0 for day in DAY_NAMES},
        "exit_streaks": {day: 0 for day in DAY_NAMES},
        "weekday_scores": {},
        "weekday_confidence": {},
        "weekday_diagnostics": {
            "mode": "unavailable-retry",
            "samples": 0,
            "raw": [],
            "stable": [],
            "top": [],
            "error": reason,
        },
        "week_returns": [],
        "history_points": 0,
        "status": "unavailable-retry",
        "computed_for": None,
        "target_date": computed_for,
        "attempt_count": attempt_count,
        "last_error": reason,
        "last_attempt_at": now.isoformat(),
    }


def context_is_complete(raw: Mapping[str, Any] | None, day: str) -> bool:
    item = raw or {}
    return item.get("status") == "complete" and item.get("computed_for") == day


def context_for_coin(state: Mapping[str, Any], display: str) -> tuple[Seasonality, list[float]]:
    raw = (state.get("coins") or {}).get(display) or {}
    seasonality = seasonality_from_dict(raw.get("seasonality"))
    returns = [float(value) for value in raw.get("week_returns", [])]
    return seasonality, returns
