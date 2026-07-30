"""Persistent three-minute signal states and score acceleration for v3.5."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping

STATE_VERSION = "signal-v350-r1"  # schema unchanged; logic revision r5


@dataclass(frozen=True)
class SignalState:
    display: str
    state: str
    direction: str
    color: str
    strength_count: int
    ranking_score: float
    entry_score: float
    exit_score: float
    score_velocity: float
    confirmation_count: int
    qualified_entry: bool
    qualified_exit: bool
    fallback_eligible: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "coins": {}}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "coins": {}}
    coins = raw.get("coins") if isinstance(raw.get("coins"), dict) else {}
    try:
        run_count = max(0, int(raw.get("run_count") or 0))
    except (TypeError, ValueError):
        run_count = 0
    return {"version": STATE_VERSION, "coins": coins, "run_count": run_count}


def _save(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _nearest(history: list[dict[str, Any]], target_ms: int, tolerance_ms: int) -> dict[str, Any] | None:
    valid = [
        item for item in history
        if isinstance(item, dict)
        and isinstance(item.get("timestamp_ms"), (int, float))
        and abs(int(item["timestamp_ms"]) - target_ms) <= tolerance_ms
    ]
    return min(valid, key=lambda item: abs(int(item["timestamp_ms"]) - target_ms)) if valid else None


def update_signal_states(
    *,
    path: Path,
    assessments: Mapping[str, Mapping[str, Any]],
    now_ms: int,
    config: Mapping[str, Any],
) -> tuple[dict[str, SignalState], dict[str, Any]]:
    section = config.get("signal_state") if isinstance(config, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    retention_minutes = max(90, int(section.get("retention_minutes", 360)))
    state = _load(path)
    raw_coins = state.setdefault("coins", {})
    # Older v3.5 caches did not yet store a global counter. Derive maturity from
    # their existing histories so the already-generated cache remains useful.
    derived_runs = 0
    for raw_coin in raw_coins.values():
        if isinstance(raw_coin, Mapping) and isinstance(raw_coin.get("history"), list):
            derived_runs = max(derived_runs, len(raw_coin["history"]))
    run_count = max(int(state.get("run_count") or 0), derived_runs) + 1
    cutoff = now_ms - retention_minutes * 60_000
    result: dict[str, SignalState] = {}

    entry_blue = float(section.get("entry_blue", 38.0))
    entry_green = float(section.get("entry_green", 60.0))
    entry_purple = float(section.get("entry_purple", 80.0))
    exit_orange = float(section.get("exit_orange", 48.0))
    exit_red = float(section.get("exit_red", 70.0))
    green_confirmations = max(1, int(section.get("green_confirmations", 2)))
    red_confirmations = max(1, int(section.get("red_confirmations", 2)))
    acceleration_cap = max(0.0, float(section.get("acceleration_bonus_cap", 12.0)))

    for display, assessment in assessments.items():
        key = str(display).upper()
        previous = raw_coins.get(key) if isinstance(raw_coins.get(key), dict) else {}
        history = previous.get("history") if isinstance(previous.get("history"), list) else []
        history = [
            item for item in history
            if isinstance(item, dict) and int(item.get("timestamp_ms") or 0) >= cutoff
        ]
        history.sort(key=lambda item: int(item.get("timestamp_ms") or 0))

        entry = _clamp(float(assessment.get("entry_score") or 0.0))
        exit_ = _clamp(float(assessment.get("exit_score") or 0.0))
        entry_deltas: list[tuple[float, float]] = []
        exit_deltas: list[tuple[float, float]] = []
        for minutes, weight in ((3, 0.45), (6, 0.35), (12, 0.20)):
            prior = _nearest(history, now_ms - minutes * 60_000, max(90_000, int(minutes * 0.45 * 60_000)))
            if prior is None:
                continue
            entry_deltas.append((entry - float(prior.get("entry", entry)), weight))
            exit_deltas.append((exit_ - float(prior.get("exit", exit_)), weight))
        def weighted(values: list[tuple[float, float]]) -> float:
            if not values:
                return 0.0
            total = sum(weight for _, weight in values)
            return sum(value * weight for value, weight in values) / total
        entry_velocity = weighted(entry_deltas)
        exit_velocity = weighted(exit_deltas)
        dominant_entry = entry >= exit_
        velocity = entry_velocity if dominant_entry else exit_velocity
        acceleration = max(-acceleration_cap, min(acceleration_cap, velocity * 0.75))
        entry_adjusted = _clamp(entry + max(-8.0, min(acceleration_cap, entry_velocity * 0.75)))
        exit_adjusted = _clamp(exit_ + max(-8.0, min(acceleration_cap, exit_velocity * 0.75)))

        raw_entry_qualified = bool(assessment.get("qualified_entry", False))
        raw_exit_qualified = bool(assessment.get("qualified_exit", False))
        discount_qualified = bool(assessment.get("discount_qualified", False))
        stabilized_after_drop = bool(assessment.get("stabilized_after_drop", False))
        demand_confirmed = bool(assessment.get("demand_confirmed", False))
        confirmed_recovery = bool(assessment.get("confirmed_recovery", False))
        falling = bool(assessment.get("falling_knife", False))
        late = bool(assessment.get("late_entry", False))
        data_conf = float(assessment.get("data_confidence") or 0.0)
        execution_quality = float(assessment.get("execution_quality_score") or 50.0)
        spread_pct = assessment.get("spread_pct")
        spread_block = spread_pct is not None and float(spread_pct) >= float(section.get("spread_block_pct", 1.0))

        # The opportunity engine already applies the complete blue-stage safety
        # gate.  Do not silently reapply the stricter legacy recovery gate here,
        # otherwise every valid early blue setup disappears.
        if falling or late or spread_block:
            raw_entry_qualified = False
        if data_conf < 0.55:
            raw_entry_qualified = raw_exit_qualified = False
        if raw_entry_qualified and not confirmed_recovery:
            entry_adjusted = min(entry_adjusted, entry_green - 0.25)

        # A formally qualified side wins over an unqualified numerical counter-score.
        # Previously a valid buy could vanish merely because the raw exit score was
        # one point higher, even though no sell conditions were actually qualified.
        if raw_entry_qualified and not raw_exit_qualified:
            side = "BUY"
        elif raw_exit_qualified and not raw_entry_qualified:
            side = "SELL"
        elif raw_entry_qualified and raw_exit_qualified:
            side = "SELL" if exit_adjusted >= entry_adjusted + 7.0 else "BUY"
        else:
            side = "BUY" if entry_adjusted >= exit_adjusted else "SELL"
        previous_side = str(previous.get("side") or "")
        previous_confirm = int(previous.get("confirmation_count") or 0)
        confirmation = previous_confirm + 1 if side == previous_side else 1

        qualified_entry = raw_entry_qualified and side == "BUY"
        qualified_exit = raw_exit_qualified and side == "SELL"
        reasons = [str(value) for value in assessment.get("reasons", []) if str(value)]
        if velocity >= 4.0:
            reasons.append("Score beschleunigt")
        elif velocity <= -4.0:
            reasons.append("Score verliert Tempo")

        if qualified_entry:
            direction = "▲"
            if confirmed_recovery and entry_adjusted >= entry_purple and confirmation >= green_confirmations and execution_quality >= 55.0:
                color, state_name = "🟣", "CONFIRMED_BUY"
            elif confirmed_recovery and entry_adjusted >= entry_green and confirmation >= green_confirmations:
                color, state_name = "🟢", "BUY"
            else:
                color, state_name = "🔵", "WATCH_BUY"
            score = entry_adjusted
        elif qualified_exit:
            direction = "▼"
            if exit_adjusted >= exit_red and (confirmation >= red_confirmations or falling):
                color, state_name = "🔴", "CONFIRMED_SELL"
            else:
                color, state_name = "🟠", "SELL"
            score = exit_adjusted
        else:
            direction = "▲" if side == "BUY" else "▼"
            color = "🔵" if side == "BUY" else "🟠"
            state_name = "WATCH_BUY" if side == "BUY" else "WATCH_SELL"
            score = max(entry_adjusted, exit_adjusted)

        strength = min(8, max(1, int(round(score / 12.5))))
        category_score = float(assessment.get("category_score") or 0.0)
        cheap_score = float(assessment.get("cheap_price_score") or 0.0)
        relative_bargain_score = float(assessment.get("relative_bargain_score") or 0.0)
        relative_discount = bool(assessment.get("relative_discount_qualified", False))
        balanced_value = bool(assessment.get("balanced_value_qualified", False))
        balanced_value_score = float(assessment.get("balanced_value_score") or 0.0)
        laggard_score = float(assessment.get("laggard_score") or 0.0)
        stabilization_score = float(assessment.get("stabilization_score") or 0.0)
        demand_score = float(assessment.get("demand_score") or 0.0)
        room_score = float(assessment.get("room_to_target_score") or 0.0)
        category_fading = float(assessment.get("category_fading_score") or 0.0)
        buy_candidate_ready = bool(assessment.get("buy_candidate_ready", False))
        early_buy_fallback = bool(
            (side == "BUY" or buy_candidate_ready)
            and not falling
            and not late
            and category_score >= 48.0
            and (
                cheap_score >= 38.0
                or relative_discount
                or balanced_value
                or (balanced_value_score >= 15.0 and stabilization_score >= 52.0 and demand_score >= 58.0 and room_score >= 25.0)
                or (relative_bargain_score >= 34.0 and laggard_score >= 8.0)
            )
            and stabilization_score >= 32.0
            and demand_score >= 44.0
            and room_score >= 20.0
            and (buy_candidate_ready or entry_adjusted >= exit_adjusted + 1.5)
        )
        real_sell_fallback = bool(
            side == "SELL"
            and exit_adjusted >= 36.0
            and exit_adjusted >= entry_adjusted + 3.0
            and (falling or category_fading >= 12.0 or raw_exit_qualified)
        )
        fallback_eligible = bool(
            data_conf >= 0.55
            and not spread_block
            and (raw_entry_qualified or early_buy_fallback or raw_exit_qualified or real_sell_fallback)
            and score >= float(section.get("fallback_minimum_score", 24.0))
        )
        ranking = score + max(0.0, velocity) * 0.45 + (2.0 if confirmation >= 2 else 0.0)
        result[key] = SignalState(
            display=key,
            state=state_name,
            direction=direction,
            color=color,
            strength_count=strength,
            ranking_score=round(_clamp(ranking), 4),
            entry_score=round(entry_adjusted, 4),
            exit_score=round(exit_adjusted, 4),
            score_velocity=round(velocity, 4),
            confirmation_count=confirmation,
            qualified_entry=qualified_entry,
            qualified_exit=qualified_exit,
            fallback_eligible=fallback_eligible,
            reasons=tuple(dict.fromkeys(reasons)),
        )

        snapshot = {"timestamp_ms": int(now_ms), "entry": round(entry, 4), "exit": round(exit_, 4)}
        if history and now_ms - int(history[-1].get("timestamp_ms") or 0) < 60_000:
            history[-1] = snapshot
        else:
            history.append(snapshot)
        raw_coins[key] = {
            "side": side,
            "state": state_name,
            "confirmation_count": confirmation,
            "updated_at_ms": int(now_ms),
            "history": history[-max(30, retention_minutes // 3):],
        }

    _save(path, {
        "version": STATE_VERSION,
        "updated_at_ms": int(now_ms),
        "run_count": int(run_count),
        "coins": raw_coins,
    })
    return result, {
        "version": STATE_VERSION,
        "run_count": int(run_count),
        "coins": len(result),
        "qualified_entries": sum(item.qualified_entry for item in result.values()),
        "qualified_exits": sum(item.qualified_exit for item in result.values()),
        "fallback_eligible": sum(item.fallback_eligible for item in result.values()),
    }
# Package revision: v3.6.0-lighter-structure-r2
