# r2
"""Episode-based NEAR/TRY/NOW outcome evaluation for CF v7.0.0.

One continuous directional setup is one statistical episode, not one sample per
minute.  Exact start features are retained so future threshold comparisons can
replay 51/58/69-like cutoffs on the same observed signals without fabricating
new historical inputs.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

STATE_VERSION = "signal-evaluation-v700-r1"
TRACKED_ACTIONS = {"NEAR", "TRY", "NOW"}
ACTION_TIER = {"NEAR": 1, "TRY": 2, "NOW": 3}
HORIZONS = (3, 5, 10, 20)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _minute_ms(signal: Any | None, now: datetime) -> int:
    candle_ms = int(getattr(signal, "candle_timestamp_ms", 0) or 0) if signal is not None else 0
    if candle_ms > 0:
        return candle_ms - candle_ms % 60_000
    stamp_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
    return stamp_ms - stamp_ms % 60_000


def _direction(signal: Any | None) -> int:
    value = _f(getattr(signal, "direction", 0.0), 0.0) if signal is not None else 0.0
    return 1 if value > 0 else -1 if value < 0 else 0


def _setup(signal: Any | None) -> str:
    return str(getattr(signal, "selected_setup", "NONE") or "NONE") if signal is not None else "NONE"


def _selected_setup_obj(signal: Any) -> Any | None:
    return {
        "EARLY": getattr(signal, "early", None),
        "TREND": getattr(signal, "trend", None),
        "REVERSAL": getattr(signal, "reversal", None),
    }.get(_setup(signal))


def _load(path: Path) -> dict[str, Any]:
    blank = {"version": STATE_VERSION, "active": {}, "pending": [], "buckets": {}, "recent_completed": []}
    if not path.exists():
        return blank
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return blank
    if (
        not isinstance(raw, dict)
        or raw.get("version") != STATE_VERSION
        or not isinstance(raw.get("active"), dict)
        or not isinstance(raw.get("pending"), list)
        or not isinstance(raw.get("buckets"), dict)
        or not isinstance(raw.get("recent_completed"), list)
    ):
        return blank
    return raw


def _save(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _return_pct(start_price: float, current_price: float, direction: int) -> float | None:
    if start_price <= 0 or current_price <= 0 or direction not in {-1, 1}:
        return None
    return (current_price / start_price - 1.0) * 100.0 * direction


def _window_snapshot(signal: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    windows = getattr(signal, "windows", {}) or {}
    for minutes in (5, 20, 60):
        row = windows.get(minutes)
        if row is None:
            continue
        result[str(minutes)] = {
            "price_pct": getattr(row, "price_pct", None),
            "volume_ratio": getattr(row, "volume_ratio", None),
            "score": _f(getattr(row, "score", 0.0)),
            "quality": str(getattr(row, "quality", "invalid") or "invalid"),
        }
    return result


def _feature_snapshot(signal: Any, action: str) -> dict[str, Any]:
    setup_obj = _selected_setup_obj(signal)
    speed = _f(getattr(signal, "swing_speed_score", 0.0))
    activity = _f(getattr(signal, "live_activity_score", 0.0))
    two_sided = _f(getattr(signal, "two_sided_score", 0.0))
    timing = speed * 0.42 + activity * 0.43 + two_sided * 0.15
    return {
        "action": action,
        "base_readiness": _f(getattr(signal, "base_trade_readiness", getattr(signal, "trade_readiness", 0.0))),
        "readiness": _f(getattr(signal, "trade_readiness", 0.0)),
        "confidence": _f(getattr(signal, "confidence", 0.0)),
        "opportunity": _f(getattr(signal, "opportunity", 0.0)),
        "setup_score": _f(getattr(setup_obj, "score", 0.0)),
        "setup_phase": str(getattr(setup_obj, "phase", "none") or "none"),
        "setup_age_minutes": getattr(setup_obj, "age_minutes", None),
        "setup_consumed_fraction": _f(getattr(setup_obj, "recovery_fraction", 0.0)),
        "volume_confirmation": _f(getattr(signal, "volume_confirmation", 0.0)),
        "tape_quality": _f(getattr(signal, "tape_quality", 0.0)),
        "execution_score": _f(getattr(signal, "execution_score", 0.0)),
        "liquidity_score": _f(getattr(signal, "liquidity_score", 0.0)),
        "data_quality": _f(getattr(signal, "data_quality", 0.0)),
        "cost_pct": getattr(signal, "cost_pct", None),
        "funding_hourly_pct": getattr(signal, "funding_hourly_pct", None),
        "btc_context": getattr(signal, "btc_context", None),
        "regime_available": bool(getattr(signal, "regime_available", False)),
        "regime_score": _f(getattr(signal, "regime_score", 0.0)),
        "regime_modifier": _f(getattr(signal, "regime_modifier", 0.0)),
        "extremity_available": bool(getattr(signal, "extremity_available", False)),
        "extremity_score": _f(getattr(signal, "extremity_score", 0.0)),
        "springer_class": str(getattr(signal, "springer_class", "") or ""),
        "springer_available": bool(getattr(signal, "springer_available", False)),
        "springer_score": _f(getattr(signal, "springer_score", 0.0)),
        "springer_reliability": _f(getattr(signal, "springer_reliability", 0.0)),
        "event_score_available": bool(getattr(signal, "event_score_available", True)),
        "event_risk": _f(getattr(signal, "event_risk", 0.0)),
        "event_kind": str(getattr(signal, "event_kind", "") or ""),
        "event_source_coverage": _f(getattr(signal, "event_source_coverage", 1.0), 1.0),
        "timing_score": max(0.0, min(100.0, timing)),
        "state_limited_by_setup": bool(getattr(signal, "state_limited_by_setup", False)),
        "state_limited_by_guard": bool(getattr(signal, "state_limited_by_guard", False)),
        "windows": _window_snapshot(signal),
    }


def _new_episode(signal: Any, action: str, now: datetime) -> dict[str, Any]:
    minute = _minute_ms(signal, now)
    symbol = str(getattr(signal, "symbol", "")).upper()
    direction = _direction(signal)
    return {
        "id": f"{symbol}:{minute}:{direction}:{_setup(signal)}",
        "symbol": symbol,
        "alias": str(getattr(signal, "alias", symbol[:3])),
        "direction": direction,
        "setup": _setup(signal),
        "started_minute_ms": minute,
        "last_matching_minute_ms": minute,
        "ended_minute_ms": None,
        "start_price": _f(getattr(signal, "price", 0.0)),
        "start_action": action,
        "max_action": action,
        "max_tier": ACTION_TIER.get(action, 0),
        "promotions": [],
        "starting_streak": int(getattr(signal, "action_streak_count", 1) or 1),
        "start_features": _feature_snapshot(signal, action),
        "peak_readiness": _f(getattr(signal, "trade_readiness", 0.0)),
        "max_favorable_pct": 0.0,
        "max_adverse_pct": 0.0,
        "reversed_after": None,
        "horizons": {},
    }


def _observe_price(row: dict[str, Any], signal: Any | None, now: datetime) -> tuple[int, float | None]:
    minute = _minute_ms(signal, now)
    price = _f(getattr(signal, "price", 0.0)) if signal is not None else 0.0
    outcome = _return_pct(_f(row.get("start_price")), price, int(row.get("direction", 0)))
    if outcome is not None:
        row["max_favorable_pct"] = max(_f(row.get("max_favorable_pct")), outcome)
        row["max_adverse_pct"] = min(_f(row.get("max_adverse_pct")), outcome)
        elapsed = max(0, int((minute - int(row.get("started_minute_ms", minute))) // 60_000))
        horizons = row.setdefault("horizons", {})
        for horizon in HORIZONS:
            key = str(horizon)
            if key in horizons or elapsed < horizon:
                continue
            horizons[key] = {
                "return_pct": round(outcome, 8),
                "observed_after_minutes": elapsed,
                "max_favorable_pct": round(_f(row.get("max_favorable_pct")), 8),
                "max_adverse_pct": round(_f(row.get("max_adverse_pct")), 8),
            }
    return minute, outcome


def _bucket_keys(row: Mapping[str, Any]) -> list[str]:
    symbol = str(row.get("symbol", ""))
    action = str(row.get("start_action", ""))
    setup = str(row.get("setup", "NONE"))
    direction = "L" if int(row.get("direction", 0)) > 0 else "S"
    return [
        f"{symbol}|{action}|{setup}|{direction}",
        f"{symbol}|{action}|ALL|{direction}",
        f"ALL|{action}|{setup}|{direction}",
        f"ALL|{action}|ALL|{direction}",
    ]


def _update_bucket(bucket: dict[str, Any], row: Mapping[str, Any]) -> None:
    bucket["episodes"] = int(bucket.get("episodes", 0)) + 1
    bucket["promoted"] = int(bucket.get("promoted", 0)) + int(bool(row.get("promotions")))
    bucket["reversed"] = int(bucket.get("reversed", 0)) + int(row.get("reversed_after") is not None)
    bucket["sum_duration_minutes"] = _f(bucket.get("sum_duration_minutes")) + _f(row.get("duration_minutes"))
    features = row.get("start_features") if isinstance(row.get("start_features"), Mapping) else {}
    bucket["sum_start_readiness"] = _f(bucket.get("sum_start_readiness")) + _f(features.get("readiness"))
    horizons = bucket.setdefault("horizons", {})
    for horizon, outcome in (row.get("horizons") or {}).items():
        if not isinstance(outcome, Mapping) or outcome.get("return_pct") is None:
            continue
        stats = horizons.setdefault(str(horizon), {"episodes": 0, "positive": 0, "sum_return_pct": 0.0, "sum_mfe_pct": 0.0, "sum_mae_pct": 0.0})
        value = _f(outcome.get("return_pct"))
        stats["episodes"] = int(stats.get("episodes", 0)) + 1
        stats["positive"] = int(stats.get("positive", 0)) + int(value > 0)
        stats["sum_return_pct"] = _f(stats.get("sum_return_pct")) + value
        stats["sum_mfe_pct"] = _f(stats.get("sum_mfe_pct")) + _f(outcome.get("max_favorable_pct"))
        stats["sum_mae_pct"] = _f(stats.get("sum_mae_pct")) + _f(outcome.get("max_adverse_pct"))


def _finalize(state: dict[str, Any], row: dict[str, Any], recent_limit: int) -> None:
    if row.get("ended_minute_ms") is None:
        row["ended_minute_ms"] = int(row.get("last_matching_minute_ms", row.get("started_minute_ms", 0)))
    row["duration_minutes"] = max(0, int((int(row["ended_minute_ms"]) - int(row["started_minute_ms"])) // 60_000))
    for key in _bucket_keys(row):
        _update_bucket(state.setdefault("buckets", {}).setdefault(key, {}), row)
    compact = {key: row.get(key) for key in (
        "id", "symbol", "alias", "direction", "setup", "started_minute_ms", "ended_minute_ms",
        "duration_minutes", "start_price", "start_action", "max_action", "promotions", "starting_streak",
        "start_features", "peak_readiness", "max_favorable_pct", "max_adverse_pct", "reversed_after", "horizons",
    )}
    recent = list(state.get("recent_completed") or [])
    recent.append(compact)
    state["recent_completed"] = recent[-recent_limit:]


def update_signal_evaluation(
    signals: Iterable[Any],
    *,
    state_path: Path,
    now: datetime,
    action_getter: Callable[[Any], str],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Update signal episodes and 3/5/10/20-minute outcomes."""
    config = config or {}
    gap_minutes = max(0, min(10, int(config.get("signal_evaluation_episode_gap_minutes", 2))))
    recent_limit = max(50, min(1000, int(config.get("signal_evaluation_recent_limit", 300))))
    state = _load(state_path)
    current = {
        str(getattr(signal, "symbol", "")).upper(): signal
        for signal in signals
        if str(getattr(signal, "symbol", "")).strip()
    }
    active = {str(k): dict(v) for k, v in (state.get("active") or {}).items() if isinstance(v, Mapping)}
    pending = [dict(row) for row in (state.get("pending") or []) if isinstance(row, Mapping)]
    now_minute = _minute_ms(None, now)
    created = 0
    ended = 0
    finalized = 0

    # Pending episodes remain observable until the longest horizon is known.
    still_pending: list[dict[str, Any]] = []
    for row in pending:
        signal = current.get(str(row.get("symbol", "")).upper())
        _observe_price(row, signal, now)
        elapsed = max(0, int((now_minute - int(row.get("started_minute_ms", now_minute))) // 60_000))
        if elapsed >= max(HORIZONS):
            _finalize(state, row, recent_limit)
            finalized += 1
        else:
            still_pending.append(row)

    for symbol in sorted(set(current) | set(active)):
        signal = current.get(symbol)
        action = str(action_getter(signal) or "") if signal is not None else ""
        direction = _direction(signal)
        setup = _setup(signal)
        minute = _minute_ms(signal, now)
        row = active.get(symbol)

        if row is not None:
            _observe_price(row, signal, now)
            same_identity = (
                action in TRACKED_ACTIONS
                and direction == int(row.get("direction", 0))
                and setup == str(row.get("setup", "NONE"))
            )
            if same_identity:
                row["last_matching_minute_ms"] = minute
                row["peak_readiness"] = max(_f(row.get("peak_readiness")), _f(getattr(signal, "trade_readiness", 0.0)))
                tier = ACTION_TIER.get(action, 0)
                if tier > int(row.get("max_tier", 0)):
                    elapsed = max(0, int((minute - int(row.get("started_minute_ms", minute))) // 60_000))
                    row.setdefault("promotions", []).append({"action": action, "after_minutes": elapsed, "readiness": _f(getattr(signal, "trade_readiness", 0.0))})
                    row["max_tier"] = tier
                    row["max_action"] = action
                active[symbol] = row
                continue

            if action in TRACKED_ACTIONS and direction == -int(row.get("direction", 0)) and row.get("reversed_after") is None:
                row["reversed_after"] = max(0, int((minute - int(row.get("started_minute_ms", minute))) // 60_000))

            gap = max(0, int((minute - int(row.get("last_matching_minute_ms", minute))) // 60_000))
            hard_change = action in TRACKED_ACTIONS and (direction != int(row.get("direction", 0)) or setup != str(row.get("setup", "NONE")))
            if hard_change or gap > gap_minutes:
                row["ended_minute_ms"] = int(row.get("last_matching_minute_ms", minute))
                active.pop(symbol, None)
                elapsed = max(0, int((now_minute - int(row.get("started_minute_ms", now_minute))) // 60_000))
                if elapsed >= max(HORIZONS):
                    _finalize(state, row, recent_limit)
                    finalized += 1
                else:
                    still_pending.append(row)
                ended += 1
            else:
                active[symbol] = row
                continue

        if signal is None:
            continue
        price = _f(getattr(signal, "price", 0.0))
        if action not in TRACKED_ACTIONS or direction == 0 or price <= 0:
            continue
        active[symbol] = _new_episode(signal, action, now)
        created += 1

    state.update({
        "version": STATE_VERSION,
        "updated_at": now.astimezone(timezone.utc).isoformat(),
        "active": active,
        "pending": still_pending,
    })
    _save(state_path, state)
    completed = list(state.get("recent_completed") or [])
    return {
        "version": STATE_VERSION,
        "active": len(active),
        "pending": len(still_pending),
        "created": created,
        "ended": ended,
        "finalized": finalized,
        "completed_total": sum(
            int(bucket.get("episodes", 0))
            for key, bucket in (state.get("buckets") or {}).items()
            if key.startswith("ALL|") and "|ALL|" in key
        ),
        "recent_completed": completed[-10:],
        "threshold_observations": [
            {
                "symbol": row.get("symbol"),
                "action": row.get("start_action"),
                "setup": row.get("setup"),
                "direction": row.get("direction"),
                "readiness": (row.get("start_features") or {}).get("readiness"),
                "base_readiness": (row.get("start_features") or {}).get("base_readiness"),
                "horizons": row.get("horizons"),
            }
            for row in completed[-100:]
        ],
        "buckets": state.get("buckets", {}),
    }
