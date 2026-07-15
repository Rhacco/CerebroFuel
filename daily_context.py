"""Stable complete-week weekday context for crypto-signal-monitor v3.2.7."""

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

STATE_VERSION = "3.2.7"
STATE_REVISION = "complete-weeks-market-r2"


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
            str(key): float(value)
            for key, value in (item.get("weekday_confidence") or {}).items()
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


def history_from_context(raw: Mapping[str, Any] | None) -> list[PricePoint]:
    points: list[PricePoint] = []
    for item in (raw or {}).get("history", []):
        try:
            timestamp, rate, volume = item
            points.append(
                PricePoint(
                    int(timestamp),
                    float(rate),
                    None if volume is None else float(volume),
                )
            )
        except (TypeError, ValueError):
            continue
    return sorted({point.timestamp_ms: point for point in points}.values(), key=lambda p: p.timestamp_ms)


def compact_history(points: Sequence[PricePoint], *, keep_after_ms: int) -> list[list[Any]]:
    return [
        [point.timestamp_ms, round(point.rate, 12), None if point.volume is None else round(point.volume, 4)]
        for point in points
        if point.timestamp_ms >= keep_after_ms
    ]


def _streaks(raw: Mapping[str, Any] | None, field: str) -> dict[str, int]:
    source = (raw or {}).get(field) or {}
    return {name: max(0, int(source.get(name, 0))) for name in DAY_NAMES}


def _ranked_raw_days(raw: Seasonality) -> list[str]:
    candidates = list(dict.fromkeys(raw.best_weekdays))
    # A robust positive fallback prevents an initially empty display when the
    # strongest day narrowly misses one secondary regime threshold. It still
    # needs a positive quality score and usable confidence.
    if not candidates:
        fallback = [
            day for day in DAY_NAMES
            if raw.weekday_scores.get(day, float("-inf")) >= 0.004
            and raw.weekday_confidence.get(day, 0.0) >= 0.40
        ]
        fallback.sort(
            key=lambda day: (
                raw.weekday_scores.get(day, -999.0),
                raw.weekday_confidence.get(day, 0.0),
            ),
            reverse=True,
        )
        if fallback:
            candidates.append(fallback[0])
    candidates.sort(
        key=lambda day: (
            raw.weekday_scores.get(day, -999.0),
            raw.weekday_confidence.get(day, 0.0),
        ),
        reverse=True,
    )
    return candidates


def _stable_days(
    raw: Seasonality,
    previous: Mapping[str, Any] | None,
    *,
    enter_days: int,
    exit_days: int,
    bootstrap_second_score: float,
    bootstrap_second_confidence: float,
) -> tuple[tuple[str, ...], dict[str, int], dict[str, int], bool, str]:
    """Freeze good weekdays daily and dampen additions/removals across days.

    On the first valid calculation the strongest robust candidate appears
    immediately. A second candidate needs clearly stronger evidence. Later
    additions/removals need consecutive daily confirmations.
    """
    previous = previous or {}
    previous_days = tuple(
        day for day in (str(value) for value in previous.get("stable_best_weekdays", []))
        if day in DAY_NAMES
    )
    enter = _streaks(previous, "enter_streaks")
    exit_ = _streaks(previous, "exit_streaks")
    initialized = bool(previous.get("weekday_initialized", False))
    ranked = _ranked_raw_days(raw)
    raw_set = set(ranked)

    for day in DAY_NAMES:
        if day in raw_set:
            enter[day] = enter.get(day, 0) + 1
            exit_[day] = 0
        else:
            enter[day] = 0
            exit_[day] = exit_.get(day, 0) + 1

    if not initialized:
        selected: list[str] = []
        if ranked:
            selected.append(ranked[0])
        if len(ranked) >= 2:
            second = ranked[1]
            first = ranked[0]
            second_score = raw.weekday_scores.get(second, 0.0)
            first_score = raw.weekday_scores.get(first, 0.0)
            second_conf = raw.weekday_confidence.get(second, 0.0)
            first_dominates = (
                first_score >= max(0.028, second_score * 2.8)
                and raw.weekday_confidence.get(first, 0.0) >= second_conf + 0.10
            )
            if (
                second_score >= min(bootstrap_second_score, 0.0015)
                and second_conf >= min(bootstrap_second_confidence, 0.34)
                and second_score >= first_score * 0.22
                and not first_dominates
            ):
                selected.append(second)
        mode = "bootstrap"
        initialized = bool(selected)
    else:
        selected = [
            day for day in previous_days
            if exit_.get(day, 0) < max(1, exit_days)
        ]
        for index, day in enumerate(ranked):
            if day in selected:
                continue
            score = raw.weekday_scores.get(day, 0.0)
            confidence = raw.weekday_confidence.get(day, 0.0)
            first_score = raw.weekday_scores.get(ranked[0], 0.0) if ranked else 0.0
            useful_second = (
                len(selected) < 2
                and index <= 1
                and score >= max(0.0015, first_score * 0.22)
                and confidence >= 0.34
            )
            if useful_second or enter.get(day, 0) >= max(1, enter_days):
                selected.append(day)
        mode = "daily-hysteresis"

    previous_position = {day: index for index, day in enumerate(previous_days)}
    selected = sorted(
        dict.fromkeys(selected),
        key=lambda day: (
            raw.weekday_scores.get(
                day,
                float((previous.get("weekday_scores") or {}).get(day, -999.0)),
            ),
            raw.weekday_confidence.get(
                day,
                float((previous.get("weekday_confidence") or {}).get(day, 0.0)),
            ),
            -previous_position.get(day, 99),
        ),
        reverse=True,
    )[:2]
    selected.sort(key=lambda day: DISPLAY_WEEK_ORDER.index(DAY_NAMES.index(day)))
    return tuple(selected), enter, exit_, initialized, mode


def _diagnostics(raw: Seasonality, stable_days: Sequence[str], mode: str) -> dict[str, Any]:
    ranked = sorted(
        raw.weekday_scores,
        key=lambda day: (
            raw.weekday_scores.get(day, -999.0),
            raw.weekday_confidence.get(day, 0.0),
        ),
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
    reference_history: Sequence[PricePoint] | None,
    now: datetime,
    timezone: str,
    config: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    computed_for: str,
) -> dict[str, Any]:
    """Create one complete daily result, including a valid empty result."""
    raw = analyze_seasonality(
        list(history),
        now,
        timezone,
        block_hours=int(config.get("time_block_hours", 4)),
        min_samples=int(config.get("seasonality_min_samples", 10)),
        minimum_observations=int(config.get("seasonality_min_observations", 84)),
        lookback_days=int(config.get("seasonality_lookback_days", 280)),
        reference_points=(list(reference_history) if reference_history else None),
    )

    previous_dict = previous if isinstance(previous, Mapping) else None
    if raw.source.startswith("complete-weeks-insufficient") or raw.source == "completed-days-insufficient":
        # Insufficient but technically valid data is final for today. Preserve a
        # previously confirmed weekday instead of repeatedly spending credits.
        if previous_dict and previous_dict.get("stable_best_weekdays"):
            stable_days = tuple(str(day) for day in previous_dict.get("stable_best_weekdays", []))
            mode = "carry-forward-insufficient"
            initialized = bool(previous_dict.get("weekday_initialized", True))
            enter = _streaks(previous_dict, "enter_streaks")
            exit_ = _streaks(previous_dict, "exit_streaks")
        else:
            stable_days = tuple()
            mode = "valid-empty-insufficient"
            initialized = False
            enter = {day: 0 for day in DAY_NAMES}
            exit_ = {day: 0 for day in DAY_NAMES}
    else:
        stable_days, enter, exit_, initialized, mode = _stable_days(
            raw,
            previous_dict,
            enter_days=int(config.get("weekday_enter_confirmations", 2)),
            exit_days=int(config.get("weekday_exit_confirmations", 2)),
            bootstrap_second_score=float(config.get("weekday_bootstrap_second_min_score", 0.012)),
            bootstrap_second_confidence=float(
                config.get("weekday_bootstrap_second_min_confidence", 0.50)
            ),
        )

    stable = Seasonality(
        current=raw.current,
        best_weekdays=stable_days,
        samples=raw.samples,
        source=f"daily-complete-weeks-{mode}",
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
        "weekday_diagnostics": _diagnostics(raw, stable_days, mode),
        "week_returns": [round(float(value), 8) for value in returns[-420:]],
        "history_points": len(history),
        "history": compact_history(
            history,
            keep_after_ms=int((now.timestamp() - (int(config.get("daily_history_days", 280)) + 14) * 86400) * 1000),
        ),
        "method_revision": STATE_REVISION,
        "status": "complete",
        "computed_for": computed_for,
        "last_error": None,
        "last_attempt_at": now.isoformat(),
    }


def carry_forward_daily_context(
    *,
    display: str,
    api_code: str,
    previous: Mapping[str, Any] | None,
    computed_for: str,
    now: datetime,
    reason: str,
) -> dict[str, Any]:
    """Finish today's context without retry loops; keep prior valid days if possible."""
    if previous:
        carried = dict(previous)
        carried.update(
            {
                "display": display,
                "api_code": api_code,
                "status": "stale-complete",
                "computed_for": computed_for,
                "last_error": reason,
                "last_attempt_at": now.isoformat(),
            }
        )
        seasonality = dict(carried.get("seasonality") or {})
        seasonality.update(
            {
                "best_weekdays": list(carried.get("stable_best_weekdays") or []),
                "current": "?",
                "current_score": None,
                "current_confidence": 0.0,
                "source": "daily-carry-forward-api-error",
            }
        )
        carried["seasonality"] = seasonality
        diagnostics = dict(carried.get("weekday_diagnostics") or {})
        diagnostics.update({"mode": "carry-forward-api-error", "error": reason})
        carried["weekday_diagnostics"] = diagnostics
        return carried

    empty = Seasonality("?", tuple(), 0, "daily-unavailable-first-run")
    return {
        "display": display,
        "api_code": api_code,
        "seasonality": asdict(empty),
        "raw_best_weekdays": [],
        "stable_best_weekdays": [],
        "weekday_initialized": False,
        "enter_streaks": {day: 0 for day in DAY_NAMES},
        "exit_streaks": {day: 0 for day in DAY_NAMES},
        "weekday_scores": {},
        "weekday_confidence": {},
        "weekday_diagnostics": {
            "mode": "unavailable-first-run",
            "samples": 0,
            "raw": [],
            "stable": [],
            "top": [],
            "error": reason,
        },
        "week_returns": [],
        "history_points": 0,
        "history": [],
        "method_revision": STATE_REVISION,
        "status": "complete-empty",
        "computed_for": computed_for,
        "last_error": reason,
        "last_attempt_at": now.isoformat(),
    }


def context_for_coin(state: Mapping[str, Any], display: str) -> tuple[Seasonality, list[float]]:
    raw = (state.get("coins") or {}).get(display) or {}
    seasonality = seasonality_from_dict(raw.get("seasonality"))
    returns = [float(value) for value in raw.get("week_returns", [])]
    return seasonality, returns
