"""Deterministic, multi-candidate paper-trading engine for CF v3.9.3."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


STATE_SCHEMA = 1
APP_VERSION = "3.9.3"
COMPATIBLE_APP_VERSIONS = {APP_VERSION, "3.9.2", "3.9.1", "3.9.0"}
ENTRY_STATES = {"BUY": 1, "SELL": -1, "STRONG_LONG": 1, "STRONG_SHORT": -1}
IMMEDIATE_STATES = {"BUY", "SELL"}
PROBE_STATES = {"STRONG_LONG", "STRONG_SHORT"}
SUPPORT_STATES = {
    "BUY": 1,
    "STRONG_LONG": 1,
    "WATCH_LONG": 1,
    "SELL": -1,
    "STRONG_SHORT": -1,
    "WATCH_SHORT": -1,
}
SETUP_CODES = {"EARLY": "E", "TREND": "T", "REVERSAL": "W"}
LEVERAGE_STEPS = (10, 15, 20, 25, 30, 40, 50)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    value = int(_f(row.get("t")))
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return fallback


def _setup(signal: Any) -> Any:
    selected = {
        "EARLY": signal.early,
        "TREND": signal.trend,
        "REVERSAL": signal.reversal,
    }.get(signal.selected_setup)
    if selected is not None:
        return selected
    return max(
        (signal.early, signal.trend, signal.reversal),
        key=lambda item: (item.phase != "none", float(item.score)),
    )


def _direction_letter(direction: int) -> str:
    return "L" if direction > 0 else "S"


def _money(value: float, signed: bool = False, compact: bool = True) -> str:
    rounded = round(abs(value) + 1e-10, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        number = str(int(round(rounded)))
    elif abs(rounded * 10 - round(rounded * 10)) < 1e-9:
        number = f"{rounded:.1f}"
        if compact and not signed and number.startswith("0."):
            number = number[1:]
    else:
        number = f"{rounded:.2f}"
        if compact and not signed and number.startswith("0."):
            number = number[1:]
    prefix = ""
    if signed:
        prefix = "+" if value >= 0 else "-"
    return f"{prefix}{number}$"


def _levels(book: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for row in list(book.get(side) or []):
        price = _f(row.get("price"))
        size = _f(row.get("remaining_base_amount"), _f(row.get("size")))
        if price > 0 and size > 0:
            result.append((price, size))
    return sorted(result, key=lambda item: item[0], reverse=side == "bids")


def _long_entry(book: Mapping[str, Any], quote: float) -> tuple[float, float] | None:
    remaining = quote
    base = 0.0
    for price, size in _levels(book, "asks"):
        spend = min(remaining, price * size)
        base += spend / price
        remaining -= spend
        if remaining <= 1e-9:
            break
    if remaining > 1e-7 or base <= 0:
        return None
    return quote / base, base


def _short_entry(book: Mapping[str, Any], quote: float) -> tuple[float, float] | None:
    remaining = quote
    base = 0.0
    for price, size in _levels(book, "bids"):
        proceeds = min(remaining, price * size)
        base += proceeds / price
        remaining -= proceeds
        if remaining <= 1e-9:
            break
    if remaining > 1e-7 or base <= 0:
        return None
    return quote / base, base


def _close_fill(
    book: Mapping[str, Any],
    direction: int,
    base: float,
) -> float | None:
    side = "bids" if direction > 0 else "asks"
    remaining = base
    quote = 0.0
    for price, size in _levels(book, side):
        filled = min(remaining, size)
        quote += filled * price
        remaining -= filled
        if remaining <= 1e-12:
            break
    if remaining > 1e-9 or base <= 0:
        return None
    return quote / base


def _roundtrip_cost_pct(
    book: Mapping[str, Any],
    direction: int,
    entry_price: float,
    base: float,
    taker_fee_pct: float,
) -> float | None:
    exit_price = _close_fill(book, direction, base)
    if exit_price is None or entry_price <= 0:
        return None
    book_cost = (
        (entry_price - exit_price) / entry_price * 100.0
        if direction > 0
        else (exit_price - entry_price) / entry_price * 100.0
    )
    return max(0.0, book_cost) + taker_fee_pct * 2.0


@dataclass
class PaperAction:
    symbol: str
    alias: str
    kind: str
    direction: int
    margin_usd: float
    leverage: int = 0
    realized_pnl_usd: float = 0.0
    reason: str = ""
    priority: float = 0.0
    is_add: bool = False
    full_close: bool = False
    discord_visible: bool = False

    def token(self) -> str:
        if self.kind == "OPEN":
            add = "+" if self.is_add else ""
            return (
                f"{self.alias}{_direction_letter(self.direction)}:"
                f"{add}{_money(self.margin_usd)}{self.leverage}x"
            )
        if self.kind == "REVERSE":
            return (
                f"{self.alias}R{_direction_letter(self.direction)}:"
                f"{_money(self.margin_usd)}{self.leverage}x"
                f"{_money(self.realized_pnl_usd, signed=True)}"
            )
        return (
            f"{self.alias}C:{_money(self.margin_usd)}"
            f"{_money(self.realized_pnl_usd, signed=True)}"
        )


class PaperTrader:
    def __init__(self, config: Mapping[str, Any], state_path: Path) -> None:
        self.config = config
        self.state_path = state_path
        self._validate_config()
        self.logs: list[str] = []
        self.actions: list[PaperAction] = []
        self.state = self._load_state()
        self.snapshots: Mapping[str, Mapping[str, Any]] = {}
        self.signals: dict[str, Any] = {}
        self.now = datetime.now(timezone.utc)

    def _validate_config(self) -> None:
        if float(self.config.get("paper_starting_capital_usd", 0.0)) <= 0:
            raise ValueError("paper_starting_capital_usd muss positiv sein")
        positions = int(self.config.get("paper_max_positions", 0))
        if not 1 <= positions <= 3:
            raise ValueError("paper_max_positions muss zwischen eins und drei liegen")
        minimum = int(self.config.get("paper_min_leverage", 0))
        maximum = int(self.config.get("paper_max_leverage", 0))
        if not 10 <= minimum <= maximum <= 50:
            raise ValueError("Paper-Hebel müssen zwischen 10x und 50x liegen")
        line_limit = int(self.config.get("paper_action_line_max_codepoints", 0))
        discord_limit = int(self.config.get("discord_max_codepoints_per_line", 0))
        if not 12 <= line_limit <= discord_limit:
            raise ValueError("Paper-Aktionszeile überschreitet das Discord-Limit")
        per_position = float(
            self.config.get("paper_max_margin_per_position_pct", 0.0)
        )
        total_margin = float(self.config.get("paper_max_total_margin_pct", 0.0))
        total_risk = float(self.config.get("paper_max_total_risk_pct", 0.0))
        if not 0 < per_position <= total_margin <= 100 or not 0 < total_risk <= 10:
            raise ValueError("Paper-Margin- oder Risikolimit ist ungültig")
        score_keys = (
            "paper_entry_min_readiness",
            "paper_entry_min_confidence",
            "paper_probe_min_readiness",
            "paper_probe_min_confidence",
            "paper_min_tape_quality",
            "paper_additional_min_readiness",
            "paper_additional_min_confidence",
            "paper_additional_min_btc_context",
            "paper_reverse_min_readiness",
            "paper_reverse_min_confidence",
            "paper_min_execution_score",
            "paper_min_liquidity_score",
            "paper_min_volume_score",
            "paper_min_btc_context",
        )
        if any(
            not 0 <= float(self.config.get(key, -1.0)) <= 100
            for key in score_keys
        ):
            raise ValueError("Paper-Qualitätsschwellen müssen zwischen null und 100 liegen")
        early_age = int(self.config.get("paper_early_max_age_minutes", 1))
        early_used = float(self.config.get("paper_early_max_consumed_fraction", 0.55))
        max_stop = float(self.config.get("paper_max_technical_stop_pct", 1.20))
        if not 0 <= early_age <= int(self.config.get("early_max_age_minutes", 2)):
            raise ValueError("paper_early_max_age_minutes ist ungültig")
        if not 0 < early_used <= float(self.config.get("early_max_consumed_fraction", 0.66)):
            raise ValueError("paper_early_max_consumed_fraction ist ungültig")
        if not 0.10 <= max_stop <= 3.0:
            raise ValueError("paper_max_technical_stop_pct ist ungültig")
        same_direction = int(self.config.get("paper_max_same_direction_positions", 2))
        if not 1 <= same_direction <= positions:
            raise ValueError("paper_max_same_direction_positions ist ungültig")
        if float(self.config.get("paper_max_directional_notional_pct", 0.0)) <= 0:
            raise ValueError("paper_max_directional_notional_pct muss positiv sein")

    def _initial_state(self) -> dict[str, Any]:
        capital = round(float(self.config.get("paper_starting_capital_usd", 100.0)), 8)
        return {
            "schema": STATE_SCHEMA,
            "app_version": APP_VERSION,
            "starting_balance_usd": capital,
            "balance_usd": capital,
            "positions": {},
            "observations": {},
            "cooldowns": {},
            "ledger": [],
            "run_count": 0,
            "last_run_at": None,
            "last_decision_key": None,
            "last_checkpoint_at": None,
            "checkpoint_requested": True,
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._initial_state()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Paper-State ist nicht lesbar: {exc}") from exc
        positions = payload.get("positions") if isinstance(payload, dict) else None
        invalid_position = (
            isinstance(positions, dict)
            and any(
                not isinstance(position, dict)
                or int(_f(position.get("direction"))) not in {-1, 1}
                or _f(position.get("margin_usd")) <= 0
                or _f(position.get("base_size")) <= 0
                or _f(position.get("entry_price")) <= 0
                or not 1 <= int(_f(position.get("leverage"))) <= 50
                for position in positions.values()
            )
        )
        if (
            not isinstance(payload, dict)
            or int(payload.get("schema", -1)) != STATE_SCHEMA
            or payload.get("app_version") not in COMPATIBLE_APP_VERSIONS
            or not isinstance(payload.get("positions"), dict)
            or _f(payload.get("balance_usd"), -1.0) < 0
            or invalid_position
        ):
            raise RuntimeError("Paper-State ist inkompatibel oder beschädigt")
        if payload.get("app_version") == "3.9.0":
            return self._initial_state()
        if payload.get("app_version") != APP_VERSION:
            payload["app_version"] = APP_VERSION
            payload["checkpoint_requested"] = True
        return payload

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _log(self, message: str) -> None:
        self.logs.append(f"[PAPER] {message}")

    def _record(self, action: PaperAction, details: Mapping[str, Any]) -> None:
        row = {
            "timestamp": _iso(self.now),
            "action": asdict(action),
            "details": dict(details),
            "balance_usd": round(_f(self.state.get("balance_usd")), 8),
        }
        ledger = list(self.state.get("ledger") or [])
        ledger.append(row)
        self.state["ledger"] = ledger[-int(self.config.get("paper_ledger_limit", 500)):]
        self.state["checkpoint_requested"] = True

    def _observation(self, signal: Any) -> dict[str, Any]:
        observations = self.state.setdefault("observations", {})
        row = dict(observations.get(signal.symbol) or {})
        entry_direction = ENTRY_STATES.get(signal.state, 0)
        previous_direction = int(row.get("entry_direction", 0))
        previous_candle = int(row.get("candle_ms", 0))
        current_candle = int(getattr(signal, "candle_timestamp_ms", 0) or 0)
        if current_candle > previous_candle:
            streak = int(row.get("entry_streak", 0)) + 1 if entry_direction == previous_direction and entry_direction else (1 if entry_direction else 0)
            row.update(
                {
                    "entry_direction": entry_direction,
                    "entry_streak": streak,
                    "candle_ms": current_candle,
                    "state": signal.state,
                }
            )
            observations[signal.symbol] = row
        return row

    def _decision_key(self, signals: Iterable[Any]) -> str:
        stamps = [
            int(getattr(signal, "candle_timestamp_ms", 0) or 0)
            for signal in signals
            if int(getattr(signal, "candle_timestamp_ms", 0) or 0) > 0
        ]
        return str(max(stamps)) if stamps else self.now.strftime("%Y%m%d%H%M")

    def _funding_update(self, position: dict[str, Any], signal: Any | None) -> None:
        previous = _parse_time(position.get("funding_updated_at"), self.now)
        hours = max(0.0, min(6.0, (self.now - previous).total_seconds() / 3600.0))
        if hours <= 0:
            return
        rate_pct = (
            None
            if signal is None or signal.funding_hourly_pct is None
            else float(signal.funding_hourly_pct)
        )
        if rate_pct is not None:
            direction = int(position["direction"])
            cost = _f(position["notional_usd"]) * rate_pct / 100.0 * hours * direction
            position["funding_accrued_usd"] = round(
                _f(position.get("funding_accrued_usd")) + cost,
                10,
            )
            position["last_funding_hourly_pct"] = rate_pct
        else:
            self._log(f"FUNDING {position['alias']}: nicht verfügbar, Intervall mit 0 modelliert")
        position["funding_updated_at"] = _iso(self.now)

    def _mark_price(self, symbol: str) -> float | None:
        signal = self.signals.get(symbol)
        if signal is not None and _f(getattr(signal, "price", 0.0)) > 0:
            return float(signal.price)
        rows = list((self.snapshots.get(symbol) or {}).get("candles") or [])
        return _f(rows[-1].get("c")) if rows else None

    def _unrealized(self, position: Mapping[str, Any]) -> float:
        symbol = str(position["symbol"])
        base = _f(position["base_size"])
        entry = _f(position["entry_price"])
        direction = int(position["direction"])
        snapshot = self.snapshots.get(symbol) or {}
        fill = _close_fill(snapshot.get("book") or {}, direction, base)
        mark = fill if fill is not None else self._mark_price(symbol)
        if mark is None or mark <= 0:
            return 0.0
        gross = (mark - entry) * base * direction
        fee = mark * base * _f(position.get("taker_fee_pct")) / 100.0
        return gross - fee - _f(position.get("funding_accrued_usd"))

    def _equity(self) -> tuple[float, float, float]:
        balance = _f(self.state.get("balance_usd"))
        positions = list((self.state.get("positions") or {}).values())
        unrealized = sum(self._unrealized(position) for position in positions)
        margin = sum(_f(position.get("margin_usd")) for position in positions)
        equity = balance + unrealized
        return equity, equity - margin, margin

    def _planned_stop_pct(self, signal: Any) -> float:
        noise = max(0.015, _f(getattr(signal, "noise_pct", 0.015), 0.015))
        cost = max(0.0, _f(signal.cost_pct))
        if signal.selected_setup == "EARLY":
            base = max(0.14, noise * 3.1, cost * 2.8)
        elif signal.selected_setup == "REVERSAL":
            base = max(
                0.18,
                noise * 3.2,
                cost * 2.8,
                min(0.52, _f(signal.reversal.move_pct) * 0.22),
            )
        elif signal.selected_setup == "TREND":
            base = max(0.16, noise * 3.6, cost * 2.7)
        else:
            base = max(0.18, noise * 3.9, cost * 2.8)
        technical = max(0.0, _f(getattr(signal, "technical_stop_pct", 0.0)))
        maximum = float(self.config.get("paper_max_technical_stop_pct", 1.20))
        return min(maximum, max(base, technical))

    def _quality(self, signal: Any) -> float:
        setup = _setup(signal)
        btc = 58.0 if signal.btc_context is None else float(signal.btc_context)
        tape = float(getattr(signal, "tape_quality", 0.0))
        return (
            float(signal.trade_readiness) * 0.33
            + float(signal.confidence) * 0.23
            + float(setup.score) * 0.18
            + float(signal.execution_score) * 0.08
            + float(signal.liquidity_score) * 0.05
            + tape * 0.08
            + btc * 0.05
        )

    def _leverage(self, signal: Any, stop_pct: float) -> int | None:
        platform = int(math.floor(_f(getattr(signal, "platform_max_leverage", 0.0))))
        minimum = int(self.config.get("paper_min_leverage", 10))
        maximum = int(self.config.get("paper_max_leverage", 50))
        if platform < minimum:
            return None
        quality = self._quality(signal)
        quality_cap = (
            50 if quality >= 96 else 40 if quality >= 92 else
            30 if quality >= 88 else 25 if quality >= 85 else
            20 if quality >= 82 else 15 if quality >= 78 else 10
        )
        if signal.state in PROBE_STATES:
            quality_cap = min(quality_cap, int(self.config.get("paper_probe_max_leverage", 20)))
        if signal.selected_setup == "REVERSAL":
            quality_cap = min(quality_cap, 20)
        elif signal.selected_setup == "EARLY":
            quality_cap = min(quality_cap, 25 if signal.state in IMMEDIATE_STATES else 15)
        elif signal.selected_setup == "TREND":
            quality_cap = min(quality_cap, 30)
        extremity = float(getattr(signal, "extremity_score", 0.0))
        direction = ENTRY_STATES.get(signal.state, 0)
        chasing_extreme = extremity * direction
        if chasing_extreme >= 60.0:
            quality_cap = min(quality_cap, 10)
        elif chasing_extreme >= 45.0:
            quality_cap = min(quality_cap, 15)
        btc_context = 58.0 if signal.btc_context is None else float(signal.btc_context)
        if btc_context < 40:
            quality_cap = min(quality_cap, 10)
        elif btc_context < 72:
            quality_cap = min(quality_cap, 15)
        if signal.execution_score < 72 or signal.volume_confirmation < 48:
            quality_cap = min(quality_cap, 10)
        if signal.funding_hourly_pct is None:
            quality_cap = min(quality_cap, int(self.config.get("paper_missing_funding_leverage_cap", 20)))
        maintenance = max(0.0, _f(getattr(signal, "maintenance_margin_pct", 0.0)) / 100.0)
        safety = stop_pct / 100.0 + float(self.config.get("paper_liquidation_buffer_pct", 0.25)) / 100.0 + maintenance
        liquidation_cap = int(math.floor(1.0 / safety)) if safety > 0 else maximum
        event_cap = getattr(signal, "event_leverage_cap", None)
        if event_cap is not None:
            quality_cap = min(quality_cap, int(event_cap))
        cap = min(platform, maximum, quality_cap, liquidation_cap)
        valid = [step for step in LEVERAGE_STEPS if minimum <= step <= cap]
        return max(valid) if valid else None

    def _entry_block(self, signal: Any, additional: bool) -> str | None:
        if bool(getattr(signal, "event_block_new", False)):
            code = str(getattr(signal, "event_code", "Ereignis") or "Ereignis")
            return f"{code} blockiert neue Position"
        direction = ENTRY_STATES.get(signal.state)
        if direction is None:
            return "kein starkes Einstiegssignal"
        if bool(getattr(signal, "chase_warning", False)):
            return "CH!-Warnung blockiert Hinterherlaufen"
        technical_stop = max(0.0, _f(getattr(signal, "technical_stop_pct", 0.0)))
        if technical_stop > float(self.config.get("paper_max_technical_stop_pct", 1.20)):
            return "technisches Invalidationsniveau ist zu weit entfernt"
        if signal.selected_setup == "REVERSAL":
            setup = _setup(signal)
            if (
                setup.phase != "ready"
                or not bool(getattr(setup, "structural_reclaim", False))
                or not bool(getattr(setup, "relative_confirmed", True))
                or bool(getattr(setup, "new_extreme_after_event", False))
            ):
                return "W? noch nicht strukturell bestätigt"
        extremity = float(getattr(signal, "extremity_score", 0.0))
        if bool(getattr(signal, "extremity_available", False)) and extremity * direction >= 72.0:
            return "Einstieg würde einer extrem überdehnten Bewegung hinterherlaufen"
        if signal.data_quality < float(self.config.get("paper_min_data_quality", 62)):
            return "zu wenige belastbare Datenfenster"
        if float(getattr(signal, "tape_quality", 0.0)) < float(self.config.get("paper_min_tape_quality", 72)):
            return "Volumenverlauf zu lückenhaft/sprunghaft"
        if signal.cost_pct is None:
            return "Ausführungskosten nicht belastbar"
        if signal.execution_score < float(self.config.get("paper_min_execution_score", 55)):
            return "Orderbuchausführung zu teuer"
        if signal.liquidity_score < float(self.config.get("paper_min_liquidity_score", 58)):
            return "Liquidität/OI zu schwach"
        if signal.volume_confirmation < float(self.config.get("paper_min_volume_score", 48)):
            return "Volumenbestätigung zu schwach"
        btc_context = 58.0 if signal.btc_context is None else float(signal.btc_context)
        if btc_context < float(self.config.get("paper_min_btc_context", 38)):
            return "BTC/Marktbreite blockiert"
        readiness = float(signal.trade_readiness)
        confidence = float(signal.confidence)
        if signal.state in PROBE_STATES:
            minimum_readiness = float(self.config.get("paper_probe_min_readiness", 66))
            minimum_confidence = float(self.config.get("paper_probe_min_confidence", 64))
        else:
            minimum_readiness = float(self.config.get("paper_entry_min_readiness", 72))
            minimum_confidence = float(self.config.get("paper_entry_min_confidence", 68))
        if str(getattr(signal, "candidate_tier", "core")) == "test":
            minimum_readiness = max(
                minimum_readiness,
                float(self.config.get("test_candidate_minimum_readiness", 84)),
            )
            minimum_confidence = max(
                minimum_confidence,
                float(self.config.get("test_candidate_minimum_confidence", 80)),
            )
        if additional:
            minimum_readiness = max(minimum_readiness, float(self.config.get("paper_additional_min_readiness", 70)))
            minimum_confidence = max(minimum_confidence, float(self.config.get("paper_additional_min_confidence", 66)))
            if btc_context < float(self.config.get("paper_additional_min_btc_context", 50)):
                return "Zusatzkandidat ohne ausreichende Marktbestätigung"
        if readiness < minimum_readiness:
            return "Readiness unter Paperfreigabe"
        if confidence < minimum_confidence:
            return "Confidence unter Paperfreigabe"
        setup = _setup(signal)
        allowed_phases = {"ready"}
        if (
            signal.state in PROBE_STATES
            and bool(self.config.get("paper_allow_strong_phase_probe", True))
        ):
            allowed_phases.add("strong")
        if setup.exit_hint or setup.phase not in allowed_phases:
            return "Setup nicht frisch"
        if signal.selected_setup == "REVERSAL":
            if setup.age_minutes is None or setup.age_minutes < 1:
                return "W wartet auf eine geschlossene Bestätigungskerze"
        if signal.selected_setup == "EARLY":
            if setup.age_minutes is not None and setup.age_minutes > int(self.config.get("paper_early_max_age_minutes", 1)):
                return "frühe Chance für Paper-Einstieg bereits zu alt"
            if setup.recovery_fraction > float(self.config.get("paper_early_max_consumed_fraction", 0.55)):
                return "Bewegungsweg für Paper-Einstieg bereits zu weit verbraucht"
        return None

    def _target_margin(self, signal: Any, rank: int) -> float:
        quality = self._quality(signal)
        if signal.state in PROBE_STATES:
            amount = 1.5 if quality >= 84 else 1.0
        else:
            amount = 4.0 if quality >= 94 else 3.0 if quality >= 88 else 2.0 if quality >= 81 else 1.0
        if signal.selected_setup in {"REVERSAL", "EARLY"}:
            amount = min(amount, 2.0 if signal.state in IMMEDIATE_STATES else 1.0)
        if rank == 2:
            amount = max(1.0, amount - 1.0)
        elif rank >= 3:
            amount = 1.0
        equity, free, _ = self._equity()
        per_position_cap = equity * float(self.config.get("paper_max_margin_per_position_pct", 4.0)) / 100.0
        return max(0.0, min(amount, per_position_cap, free))

    def _risk_usd(self, margin: float, leverage: int, stop_pct: float, cost_pct: float) -> float:
        notional = margin * leverage
        return notional * (stop_pct + max(0.0, cost_pct)) / 100.0

    def _portfolio_allows(
        self,
        signal: Any,
        margin: float,
        leverage: int,
        stop_pct: float,
        direction: int,
        existing_symbol: str | None = None,
    ) -> str | None:
        equity, free, used_margin = self._equity()
        if margin <= 0 or free + 1e-9 < margin:
            return "freie Margin reicht nicht"
        current_margin = (
            _f(
                self.state.get("positions", {})
                .get(existing_symbol, {})
                .get("margin_usd")
            )
            if existing_symbol is not None
            else 0.0
        )
        per_position_cap = equity * float(
            self.config.get("paper_max_margin_per_position_pct", 4.0)
        ) / 100.0
        if current_margin + margin > per_position_cap + 0.05:
            return "Positionsmargin-Limit erreicht"
        margin_cap = equity * float(self.config.get("paper_max_total_margin_pct", 8.0)) / 100.0
        if used_margin + margin > margin_cap + 0.05:
            return "Gesamtmargin-Limit erreicht"
        positions = list((self.state.get("positions") or {}).values())
        existing_risk = sum(_f(position.get("risk_usd")) for position in positions)
        risk = self._risk_usd(margin, leverage, stop_pct, _f(signal.cost_pct))
        risk_cap = equity * float(self.config.get("paper_max_total_risk_pct", 1.25)) / 100.0
        if existing_risk + risk > risk_cap + 1e-9:
            return "Portfoliorisiko-Limit erreicht"
        same_direction = [
            position
            for position in positions
            if int(position.get("direction", 0)) == direction
        ]
        if existing_symbol is None and len(same_direction) >= int(
            self.config.get("paper_max_same_direction_positions", 2)
        ):
            return "Richtung bereits ausreichend belegt"
        direction_notional = sum(
            _f(position.get("notional_usd"))
            for position in same_direction
        ) + margin * leverage
        direction_cap = equity * float(
            self.config.get("paper_max_directional_notional_pct", 150.0)
        ) / 100.0
        if direction_notional > direction_cap + 1e-9:
            return "Richtungsvolumen zu hoch"
        return None

    def _price_levels(
        self,
        entry: float,
        direction: int,
        stop_pct: float,
    ) -> tuple[float, float, float]:
        sign = 1 if direction > 0 else -1
        stop = entry * (1.0 - sign * stop_pct / 100.0)
        target_one = entry * (
            1.0 + sign * stop_pct * float(
                self.config.get("paper_target_one_r", 1.15)
            ) / 100.0
        )
        target_two = entry * (
            1.0 + sign * stop_pct * float(
                self.config.get("paper_target_two_r", 2.1)
            ) / 100.0
        )
        return stop, target_one, target_two

    def _open(
        self,
        signal: Any,
        margin: float,
        leverage: int,
        rank: int,
        *,
        reverse_pnl: float | None = None,
        reverse_details: Mapping[str, Any] | None = None,
    ) -> PaperAction | None:
        symbol = signal.symbol
        direction = ENTRY_STATES[signal.state]
        snapshot = self.snapshots.get(symbol) or {}
        book = snapshot.get("book") or {}
        minimum_quote = max(0.0, _f(getattr(signal, "min_quote_amount", 0.0)))
        minimum_margin = max(1, math.ceil(minimum_quote / leverage - 1e-9))
        margin = float(max(minimum_margin, math.floor(margin + 1e-9)))
        stop_pct = self._planned_stop_pct(signal)
        block: str | None = None
        while margin >= minimum_margin:
            block = self._portfolio_allows(
                signal,
                margin,
                leverage,
                stop_pct,
                direction,
            )
            if block is None:
                break
            margin -= 1.0
        if block:
            self._log(f"SKIP {signal.alias} {_direction_letter(direction)}: {block}")
            return None
        notional = margin * leverage
        fill = _long_entry(book, notional) if direction > 0 else _short_entry(book, notional)
        if fill is None:
            self._log(f"SKIP {signal.alias} {_direction_letter(direction)}: Orderbuchtiefe reicht nicht")
            return None
        entry, base = fill
        technical_price = _f(getattr(signal, "technical_stop_price", 0.0))
        if technical_price > 0:
            if direction > 0 and technical_price < entry:
                stop_pct = max(stop_pct, (entry - technical_price) / entry * 100.0)
            elif direction < 0 and technical_price > entry:
                stop_pct = max(stop_pct, (technical_price - entry) / entry * 100.0)
        maximum_technical = float(self.config.get("paper_max_technical_stop_pct", 1.20))
        if stop_pct > maximum_technical + 1e-9:
            self._log(
                f"SKIP {signal.alias} {_direction_letter(direction)}: "
                "technisches Invalidationsniveau liegt zu weit entfernt"
            )
            return None
        maintenance = max(
            0.0,
            _f(getattr(signal, "maintenance_margin_pct", 0.0)) / 100.0,
        )
        safety = (
            stop_pct / 100.0
            + float(self.config.get("paper_liquidation_buffer_pct", 0.25)) / 100.0
            + maintenance
        )
        if safety > 0 and leverage > math.floor(1.0 / safety):
            self._log(
                f"SKIP {signal.alias} {_direction_letter(direction)}: "
                "Hebel passt nicht zum technischen Stop"
            )
            return None
        taker_fee_pct = max(0.0, _f(getattr(signal, "taker_fee_pct", 0.0)))
        actual_roundtrip_cost = _roundtrip_cost_pct(
            book,
            direction,
            entry,
            base,
            taker_fee_pct,
        )
        if (
            actual_roundtrip_cost is None
            or actual_roundtrip_cost
            > float(self.config.get("max_roundtrip_cost_pct", 0.15))
        ):
            self._log(
                f"SKIP {signal.alias} {_direction_letter(direction)}: "
                "tatsächliche Positionsgröße ist im Orderbuch zu teuer"
            )
            return None
        equity, _, _ = self._equity()
        existing_risk = sum(
            _f(position.get("risk_usd"))
            for position in self.state.get("positions", {}).values()
        )
        actual_risk = self._risk_usd(
            margin,
            leverage,
            stop_pct,
            actual_roundtrip_cost,
        )
        if existing_risk + actual_risk > (
            equity
            * float(self.config.get("paper_max_total_risk_pct", 1.25))
            / 100.0
            + 1e-9
        ):
            self._log(f"SKIP {signal.alias}: exakte Orderbuchkosten überschreiten Risikolimit")
            return None
        stop, target_one, target_two = self._price_levels(
            entry,
            direction,
            stop_pct,
        )
        entry_fee = notional * taker_fee_pct / 100.0
        self.state["balance_usd"] = round(
            _f(self.state.get("balance_usd")) - entry_fee,
            10,
        )
        setup = SETUP_CODES.get(signal.selected_setup, "+" if signal.direction >= 0 else "-")
        position = {
            "symbol": symbol,
            "alias": signal.alias,
            "direction": direction,
            "margin_usd": round(margin, 8),
            "leverage": leverage,
            "notional_usd": round(notional, 8),
            "base_size": round(base, 14),
            "entry_price": entry,
            "opened_at": _iso(self.now),
            "funding_updated_at": _iso(self.now),
            "last_candle_ms": int(getattr(signal, "candle_timestamp_ms", 0) or 0),
            "setup": setup,
            "stop_pct": stop_pct,
            "stop_price": stop,
            "target_one_price": target_one,
            "target_two_price": target_two,
            "target_one_taken": False,
            "degrade_streak": 0,
            "scale_count": 0,
            "entry_fee_remaining_usd": entry_fee,
            "funding_accrued_usd": 0.0,
            "realized_pnl_usd": 0.0,
            "taker_fee_pct": taker_fee_pct,
            "risk_usd": actual_risk,
            "entry_roundtrip_cost_pct": actual_roundtrip_cost,
            "entry_readiness": float(signal.trade_readiness),
            "entry_confidence": float(signal.confidence),
            "entry_rank": rank,
            "entry_state": signal.state,
            "probe_entry": signal.state in PROBE_STATES,
            "entry_features": {
                "setup": setup,
                "setup_phase": str(_setup(signal).phase),
                "setup_score": float(_setup(signal).score),
                "setup_age_minutes": _setup(signal).age_minutes,
                "setup_consumed_fraction": float(_setup(signal).recovery_fraction),
                "early_boundary_distance_pct": float(getattr(_setup(signal), "boundary_distance_pct", 0.0)),
                "early_approach_confirmed": bool(getattr(_setup(signal), "approach_confirmed", False)),
                "early_clean_boundary_test": bool(getattr(_setup(signal), "clean_boundary_test", False)),
                "early_preview_only": bool(getattr(_setup(signal), "preview_only", False)),
                "chase_warning": bool(getattr(signal, "chase_warning", False)),
                "readiness": float(signal.trade_readiness),
                "confidence": float(signal.confidence),
                "execution_score": float(signal.execution_score),
                "liquidity_score": float(signal.liquidity_score),
                "volume_confirmation": float(signal.volume_confirmation),
                "tape_quality": float(getattr(signal, "tape_quality", 0.0)),
                "btc_context": 58.0 if signal.btc_context is None else float(signal.btc_context),
                "cost_pct": float(actual_roundtrip_cost),
                "funding_hourly_pct": None if signal.funding_hourly_pct is None else float(signal.funding_hourly_pct),
                "funding_missing": signal.funding_hourly_pct is None,
                "event_risk": float(getattr(signal, "event_risk", 0.0)),
                "event_kind": str(getattr(signal, "event_kind", "") or ""),
                "regime_available": bool(getattr(signal, "regime_available", False)),
                "regime_score": float(getattr(signal, "regime_score", 0.0)),
                "regime_consistency": float(getattr(signal, "regime_consistency", 0.0)),
                "regime_modifier": float(getattr(signal, "regime_modifier", 0.0)),
                "return_7d": getattr(signal, "return_7d", None),
                "return_14d": getattr(signal, "return_14d", None),
                "return_30d": getattr(signal, "return_30d", None),
                "relative_7d": getattr(signal, "relative_7d", None),
                "relative_14d": getattr(signal, "relative_14d", None),
                "relative_30d": getattr(signal, "relative_30d", None),
                "rebound_participation": getattr(signal, "rebound_participation", None),
                "relative_drift_60m": getattr(signal, "relative_drift_60m", None),
                "extremity_available": bool(getattr(signal, "extremity_available", False)),
                "extremity_score": float(getattr(signal, "extremity_score", 0.0)),
                "extremity_confidence": float(getattr(signal, "extremity_confidence", 0.0)),
                "extremity_intraday": float(getattr(signal, "extremity_intraday", 0.0)),
                "extremity_swing": float(getattr(signal, "extremity_swing", 0.0)),
                "extremity_return_1d": getattr(signal, "extremity_return_1d", None),
                "extremity_return_3d": getattr(signal, "extremity_return_3d", None),
                "extremity_return_7d": getattr(signal, "extremity_return_7d", None),
                "technical_stop_price": getattr(signal, "technical_stop_price", None),
                "technical_stop_pct": getattr(signal, "technical_stop_pct", None),
                "reversal_structural_reclaim": bool(getattr(_setup(signal), "structural_reclaim", False)),
                "reversal_relative_confirmed": bool(getattr(_setup(signal), "relative_confirmed", True)),
                "reversal_relative_opposition": bool(getattr(_setup(signal), "relative_opposition", False)),
                "reversal_new_extreme": bool(getattr(_setup(signal), "new_extreme_after_event", False)),
                "data_quality": float(signal.data_quality),
                "direction": int(direction),
                "stop_pct": float(stop_pct),
                "leverage": int(leverage),
                "margin_usd": float(margin),
                "risk_usd": float(actual_risk),
                "probe_entry": signal.state in PROBE_STATES,
                "symbol": symbol,
                "rank": int(rank),
            },
        }
        self.state.setdefault("positions", {})[symbol] = position
        kind = "REVERSE" if reverse_pnl is not None else "OPEN"
        action = PaperAction(
            symbol=symbol,
            alias=signal.alias,
            kind=kind,
            direction=direction,
            margin_usd=margin,
            leverage=leverage,
            realized_pnl_usd=reverse_pnl or 0.0,
            reason=(
                f"{setup} {signal.trade_readiness:.1f}/{signal.confidence:.1f}, "
                f"Stop {stop_pct:.3f}%"
            ),
            priority=(110.0 if kind == "REVERSE" else 70.0) + self._quality(signal) / 10.0,
        )
        self.actions.append(action)
        record_details = {
            "entry_price": entry,
            "base_size": base,
            "notional_usd": notional,
            "stop_price": stop,
            "target_one_price": target_one,
            "target_two_price": target_two,
            "entry_fee_usd": entry_fee,
            "entry_roundtrip_cost_pct": actual_roundtrip_cost,
            "setup": setup,
            "readiness": signal.trade_readiness,
            "confidence": signal.confidence,
            "rank": rank,
            "entry_state": signal.state,
        }
        if reverse_details is not None:
            record_details["reverse_close"] = dict(reverse_details)
        self._record(action, record_details)
        label = "REVERSE" if kind == "REVERSE" else "OPEN"
        reverse_log = (
            f", realisiert {_money(reverse_pnl, signed=True, compact=False)}"
            if reverse_pnl is not None
            else ""
        )
        self._log(
            f"{label} {signal.alias} {_direction_letter(direction)} "
            f"{_money(margin, compact=False)} {leverage}x @ {entry:.10g}"
            f"{reverse_log} | {action.reason}"
        )
        return action

    def _close(
        self,
        position: dict[str, Any],
        close_margin: float,
        reason: str,
        priority: float,
        *,
        forced_price: float | None = None,
        record_action: bool = True,
    ) -> tuple[PaperAction, float, dict[str, Any]]:
        symbol = str(position["symbol"])
        original_margin = _f(position["margin_usd"])
        close_margin = min(original_margin, max(0.0, close_margin))
        fraction = 1.0 if close_margin >= original_margin - 1e-9 else close_margin / original_margin
        base = _f(position["base_size"]) * fraction
        direction = int(position["direction"])
        snapshot = self.snapshots.get(symbol) or {}
        exit_price = forced_price
        if exit_price is None:
            exit_price = _close_fill(snapshot.get("book") or {}, direction, base)
        if exit_price is None or exit_price <= 0:
            mark = self._mark_price(symbol) or _f(position["entry_price"])
            emergency = float(self.config.get("paper_emergency_slippage_pct", 0.05)) / 100.0
            exit_price = mark * (1.0 - emergency if direction > 0 else 1.0 + emergency)
            reason += ", Notausführung modelliert"
        entry = _f(position["entry_price"])
        gross = (exit_price - entry) * base * direction
        exit_fee = (
            exit_price
            * base
            * _f(position.get("taker_fee_pct"))
            / 100.0
        )
        funding = _f(position.get("funding_accrued_usd")) * fraction
        entry_fee = _f(position.get("entry_fee_remaining_usd")) * fraction
        raw_net = gross - exit_fee - funding - entry_fee
        loss_capped = raw_net < -close_margin
        net = max(raw_net, -close_margin)
        if loss_capped:
            reason += ", isolierte Margin ausgeschöpft"
        self.state["balance_usd"] = round(
            _f(self.state.get("balance_usd")) + net + entry_fee,
            10,
        )
        previous_realized = _f(position.get("realized_pnl_usd"))
        trade_net_pnl = previous_realized + net
        full = fraction >= 1.0 - 1e-9
        if full:
            self.state.setdefault("positions", {}).pop(symbol, None)
        else:
            position["realized_pnl_usd"] = round(trade_net_pnl, 12)
            for key in (
                "margin_usd",
                "notional_usd",
                "base_size",
                "entry_fee_remaining_usd",
                "funding_accrued_usd",
                "risk_usd",
            ):
                position[key] = round(_f(position.get(key)) * (1.0 - fraction), 12)
        action = PaperAction(
            symbol=symbol,
            alias=str(position["alias"]),
            kind="CLOSE",
            direction=direction,
            margin_usd=close_margin,
            realized_pnl_usd=net,
            reason=reason,
            priority=priority,
            full_close=full,
        )
        entry_features = dict(position.get("entry_features") or {})
        entry_features.setdefault("setup", str(position.get("setup") or ""))
        entry_features.setdefault("readiness", _f(position.get("entry_readiness")))
        entry_features.setdefault("confidence", _f(position.get("entry_confidence")))
        entry_features.setdefault("leverage", int(position.get("leverage", 0)))
        entry_features.setdefault("risk_usd", _f(position.get("risk_usd")))
        entry_features.setdefault("probe_entry", bool(position.get("probe_entry", False)))
        close_details = {
            "entry_price": entry,
            "exit_price": exit_price,
            "base_size": base,
            "gross_pnl_usd": gross,
            "entry_fee_usd": entry_fee,
            "exit_fee_usd": exit_fee,
            "funding_usd": funding,
            "raw_net_pnl_usd": raw_net,
            "trade_net_pnl_usd": trade_net_pnl,
            "isolated_loss_capped": loss_capped,
            "full_close": full,
            "entry_features": entry_features,
            "holding_minutes": max(0.0, (self.now - _parse_time(position.get("opened_at"), self.now)).total_seconds() / 60.0),
        }
        if record_action:
            self.actions.append(action)
            self._record(action, close_details)
            self._log(
                f"CLOSE {action.alias} {_money(close_margin, compact=False)} "
                f"{_money(net, signed=True, compact=False)} @ {exit_price:.10g} | {reason}"
            )
        return action, net, close_details

    def _intrabar_action(
        self,
        position: dict[str, Any],
        rows: list[Mapping[str, Any]],
    ) -> bool:
        last_stamp = int(position.get("last_candle_ms", 0))
        fresh = [row for row in rows if _timestamp_ms(row) > last_stamp]
        direction = int(position["direction"])
        stop = _f(position["stop_price"])
        target_one = _f(position["target_one_price"])
        target_two = _f(position["target_two_price"])
        stop_slippage = float(self.config.get("paper_stop_slippage_pct", 0.03)) / 100.0
        for row in fresh:
            high = _f(row.get("h"))
            low = _f(row.get("l"))
            stop_hit = low <= stop if direction > 0 else high >= stop
            target_one_hit = (
                not bool(position.get("target_one_taken"))
                and (high >= target_one if direction > 0 else low <= target_one)
            )
            target_two_hit = high >= target_two if direction > 0 else low <= target_two
            if stop_hit:
                opened = _f(row.get("o"), stop)
                trigger = (
                    min(stop, opened)
                    if direction > 0
                    else max(stop, opened)
                )
                price = trigger * (
                    1.0 - stop_slippage
                    if direction > 0
                    else 1.0 + stop_slippage
                )
                self._close(
                    position,
                    _f(position["margin_usd"]),
                    "Stop ausgelöst",
                    130.0,
                    forced_price=price,
                )
                self.state.setdefault("cooldowns", {})[str(position["symbol"])] = int(
                    self.now.timestamp()
                ) + int(self.config.get("paper_stop_cooldown_minutes", 12)) * 60
                return True
            if target_one_hit and target_two_hit:
                self._close(
                    position,
                    _f(position["margin_usd"]),
                    "beide Ziele in einer Kerze; konservativ Ziel 1",
                    100.0,
                    forced_price=target_one,
                )
                self.state.setdefault("cooldowns", {})[str(position["symbol"])] = int(
                    self.now.timestamp()
                ) + int(self.config.get("paper_close_cooldown_minutes", 5)) * 60
                return True
            if target_two_hit:
                self._close(
                    position,
                    _f(position["margin_usd"]),
                    "Ziel 2 erreicht",
                    100.0,
                    forced_price=target_two,
                )
                self.state.setdefault("cooldowns", {})[str(position["symbol"])] = int(
                    self.now.timestamp()
                ) + int(self.config.get("paper_close_cooldown_minutes", 5)) * 60
                return True
            if target_one_hit:
                margin = _f(position["margin_usd"])
                half = round(margin / 2.0, 2)
                minimum_quote = _f(
                    getattr(self.signals.get(str(position["symbol"])), "min_quote_amount", 10.0),
                    10.0,
                )
                if half * int(position["leverage"]) < minimum_quote - 1e-9:
                    half = margin
                self._close(
                    position,
                    half,
                    "Ziel 1 erreicht",
                    95.0,
                    forced_price=target_one,
                )
                if str(position["symbol"]) in self.state.get("positions", {}):
                    position["target_one_taken"] = True
                    position["last_candle_ms"] = _timestamp_ms(row)
                    entry = _f(position["entry_price"])
                    fee_buffer = max(
                        float(self.config.get("paper_break_even_buffer_pct", 0.02)),
                        _f(position.get("entry_roundtrip_cost_pct")),
                    ) / 100.0
                    position["stop_price"] = entry * (
                        1.0 + fee_buffer if direction > 0 else 1.0 - fee_buffer
                    )
                else:
                    self.state.setdefault("cooldowns", {})[str(position["symbol"])] = int(
                        self.now.timestamp()
                    ) + int(self.config.get("paper_close_cooldown_minutes", 5)) * 60
                return True
        if fresh:
            position["last_candle_ms"] = _timestamp_ms(fresh[-1])
        return False

    def _reverse_or_close(
        self,
        position: dict[str, Any],
        signal: Any,
        rank: int,
    ) -> bool:
        new_direction = ENTRY_STATES.get(signal.state, 0)
        if not new_direction or new_direction == int(position["direction"]):
            return False
        reverse_block = self._entry_block(signal, additional=False)
        strict = (
            reverse_block is None
            and signal.trade_readiness >= float(
                self.config.get("paper_reverse_min_readiness", 86)
            )
            and signal.confidence >= float(
                self.config.get("paper_reverse_min_confidence", 82)
            )
        )
        old_margin = _f(position["margin_usd"])
        close_action, pnl, close_details = self._close(
            position,
            old_margin,
            "entgegengesetzte Sofortfreigabe",
            115.0,
            record_action=not strict,
        )
        if not strict:
            self.state.setdefault("cooldowns", {})[signal.symbol] = int(
                self.now.timestamp()
            ) + int(self.config.get("paper_close_cooldown_minutes", 5)) * 60
            return True
        stop_pct = self._planned_stop_pct(signal)
        leverage = self._leverage(signal, stop_pct)
        if leverage is None:
            if not close_action.discord_visible:
                self.actions.append(close_action)
                self._record(
                    close_action,
                    close_details | {"reverse_block": "Hebel unter 10x"},
                )
            self._log(f"REVERSE {signal.alias} verworfen: Markt erlaubt keinen sicheren 10x-Hebel")
            return True
        target = min(old_margin, self._target_margin(signal, rank))
        action = self._open(
            signal,
            target,
            leverage,
            rank,
            reverse_pnl=pnl,
            reverse_details=close_details,
        )
        if action is None:
            self.actions.append(close_action)
            self._record(
                close_action,
                close_details | {"reverse_block": "Neuer Einstieg nicht ausführbar"},
            )
        return True

    def _signal_exit(self, position: dict[str, Any], signal: Any | None) -> bool:
        if signal is None or signal.state == "INVALID_DATA":
            self._log(f"HOLD {position['alias']}: keine belastbaren neuen Signaldaten")
            return False
        direction = int(position["direction"])
        support = SUPPORT_STATES.get(signal.state, 0)
        setup_now = _setup(signal)
        hard_opposition = support == -direction and signal.state in {
            "BUY", "SELL", "STRONG_LONG", "STRONG_SHORT"
        }
        exit_hint = bool(setup_now.exit_hint) and signal.selected_setup == {
            "E": "EARLY",
            "T": "TREND",
            "W": "REVERSAL",
        }.get(position.get("setup"))
        if support == direction:
            position["degrade_streak"] = 0
        else:
            position["degrade_streak"] = int(position.get("degrade_streak", 0)) + 1
        opened = _parse_time(position.get("opened_at"), self.now)
        age_minutes = max(0.0, (self.now - opened).total_seconds() / 60.0)
        max_age = {
            "E": int(self.config.get("paper_early_max_hold_minutes", 18)),
            "T": int(self.config.get("paper_trend_max_hold_minutes", 25)),
            "W": int(self.config.get("paper_reversal_max_hold_minutes", 12)),
        }.get(str(position.get("setup")), 15)
        reason = ""
        priority = 88.0
        if hard_opposition:
            reason, priority = "starkes Gegensignal", 108.0
        elif exit_hint:
            reason, priority = "Setup abgelaufen", 98.0
        elif age_minutes >= max_age:
            reason, priority = "maximale Setup-Haltedauer", 90.0
        elif int(position.get("degrade_streak", 0)) >= int(
            self.config.get("paper_exit_degrade_runs", 2)
        ):
            reason, priority = "Richtung zweimal nicht bestätigt", 92.0
        if not reason:
            unrealized = self._unrealized(position)
            self._log(
                f"HOLD {position['alias']} {_direction_letter(direction)} "
                f"{_money(unrealized, signed=True, compact=False)} | "
                f"{signal.state} {signal.trade_readiness:.1f}/{signal.confidence:.1f}"
            )
            return False
        self._close(position, _f(position["margin_usd"]), reason, priority)
        self.state.setdefault("cooldowns", {})[str(position["symbol"])] = int(
            self.now.timestamp()
        ) + int(self.config.get("paper_close_cooldown_minutes", 5)) * 60
        return True

    def _maybe_add(self, position: dict[str, Any], signal: Any, rank: int) -> None:
        if (
            ENTRY_STATES.get(signal.state) != int(position["direction"])
            or int(position.get("scale_count", 0)) >= 1
            or bool(position.get("target_one_taken"))
        ):
            return
        observation = self.state.get("observations", {}).get(signal.symbol, {})
        streak_needed = 1 if signal.state in IMMEDIATE_STATES else 2
        if int(observation.get("entry_streak", 0)) < streak_needed:
            return
        upgrade = bool(position.get("probe_entry")) and signal.state in IMMEDIATE_STATES
        if not upgrade and signal.state not in IMMEDIATE_STATES and int(observation.get("entry_streak", 0)) < 2:
            return
        target = self._target_margin(signal, rank)
        current = _f(position["margin_usd"])
        desired = max(target, current + (1.0 if upgrade else 0.0))
        add = min(1.0, max(0.0, math.floor(desired - current + 1e-9)))
        if add < 1.0:
            return
        mark = self._mark_price(signal.symbol) or _f(position["entry_price"])
        favorable = (mark / _f(position["entry_price"]) - 1.0) * 100.0 * int(position["direction"])
        if favorable > _f(position["stop_pct"]) * 0.45:
            self._log(f"HOLD {signal.alias}: Ausbau wäre bereits zu spät")
            return
        leverage = int(position["leverage"])
        stop_pct = _f(position["stop_pct"])
        block = self._portfolio_allows(signal, add, leverage, stop_pct, int(position["direction"]), existing_symbol=signal.symbol)
        if block:
            self._log(f"HOLD {signal.alias}: kein Ausbau wegen {block}")
            return
        notional = add * leverage
        book = (self.snapshots.get(signal.symbol) or {}).get("book") or {}
        fill = _long_entry(book, notional) if int(position["direction"]) > 0 else _short_entry(book, notional)
        if fill is None:
            self._log(f"HOLD {signal.alias}: Ausbau nicht ausführbar")
            return
        price, base = fill
        actual_roundtrip_cost = _roundtrip_cost_pct(book, int(position["direction"]), price, base, _f(position.get("taker_fee_pct")))
        if actual_roundtrip_cost is None or actual_roundtrip_cost > float(self.config.get("max_roundtrip_cost_pct", 0.10)):
            self._log(f"HOLD {signal.alias}: Ausbaugröße wäre im Orderbuch zu teuer")
            return
        equity, _, _ = self._equity()
        added_risk = self._risk_usd(add, leverage, stop_pct, actual_roundtrip_cost)
        portfolio_risk = sum(_f(item.get("risk_usd")) for item in self.state.get("positions", {}).values())
        if portfolio_risk + added_risk > equity * float(self.config.get("paper_max_total_risk_pct", 1.75)) / 100.0 + 1e-9:
            self._log(f"HOLD {signal.alias}: Ausbau überschreitet Risikolimit")
            return
        old_base = _f(position["base_size"])
        new_base = old_base + base
        weighted_entry = (_f(position["entry_price"]) * old_base + price * base) / new_base
        fee = notional * _f(position.get("taker_fee_pct")) / 100.0
        self.state["balance_usd"] = round(_f(self.state.get("balance_usd")) - fee, 10)
        position["margin_usd"] = round(current + add, 8)
        position["notional_usd"] = round(_f(position["notional_usd"]) + notional, 8)
        position["base_size"] = round(new_base, 14)
        position["entry_price"] = weighted_entry
        position["entry_fee_remaining_usd"] = round(_f(position.get("entry_fee_remaining_usd")) + fee, 10)
        candidate_stop, candidate_one, candidate_two = self._price_levels(weighted_entry, int(position["direction"]), stop_pct)
        if int(position["direction"]) > 0:
            position["stop_price"] = max(_f(position["stop_price"]), candidate_stop)
            position["target_one_price"] = max(_f(position["target_one_price"]), candidate_one)
            position["target_two_price"] = max(_f(position["target_two_price"]), candidate_two)
        else:
            position["stop_price"] = min(_f(position["stop_price"]), candidate_stop)
            position["target_one_price"] = min(_f(position["target_one_price"]), candidate_one)
            position["target_two_price"] = min(_f(position["target_two_price"]), candidate_two)
        position["scale_count"] = 1
        position["probe_entry"] = False
        position["risk_usd"] = round(_f(position.get("risk_usd")) + added_risk, 10)
        reason = "Probe bestätigt" if upgrade else "zweite starke Bestätigung"
        action = PaperAction(
            symbol=signal.symbol, alias=signal.alias, kind="OPEN",
            direction=int(position["direction"]), margin_usd=add, leverage=leverage,
            reason=f"{reason}; Preis noch nah am Einstieg",
            priority=80.0 + self._quality(signal) / 10.0, is_add=True,
        )
        self.actions.append(action)
        self._record(action, {"fill_price": price, "new_entry_price": weighted_entry, "entry_fee_usd": fee, "new_margin_usd": position["margin_usd"]})
        self._log(f"ADD {signal.alias} {_money(add, compact=False)} {leverage}x @ {price:.10g} | {reason}")

    def _pack_actions(self) -> str | None:
        maximum = int(
            self.config.get(
                "paper_action_line_max_codepoints",
                self.config.get("discord_max_codepoints_per_line", 34),
            )
        )
        ordered = sorted(
            enumerate(self.actions),
            key=lambda row: (-row[1].priority, row[0]),
        )
        tokens: list[str] = []
        length = 0
        blocked = False
        for _, action in ordered:
            token = action.token()
            extra = len(token) if not tokens else len(token) + 1
            if blocked or length + extra > maximum:
                blocked = True
                continue
            tokens.append(token)
            length += extra
            action.discord_visible = True
        hidden = [action for action in self.actions if not action.discord_visible]
        if hidden:
            self._log(
                "LOG-ONLY "
                + " ".join(action.token() for action in sorted(hidden, key=lambda item: -item.priority))
            )
        return " ".join(tokens) if tokens else None

    def run(
        self,
        signals: list[Any],
        snapshots: Mapping[str, Mapping[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        self.now = now.astimezone(timezone.utc)
        self.snapshots = snapshots
        self.signals = {signal.symbol: signal for signal in signals}
        self.state["cooldowns"] = {
            symbol: int(until)
            for symbol, until in (self.state.get("cooldowns") or {}).items()
            if int(until) > int(self.now.timestamp())
        }
        for signal in signals:
            self._observation(signal)
        decision_key = self._decision_key(signals)
        fresh_decision = decision_key != self.state.get("last_decision_key")
        if not fresh_decision:
            self._log(f"HOLD: Kerze {decision_key} bereits entschieden")

        ranked_index = {
            signal.symbol: index + 1
            for index, signal in enumerate(signals)
        }
        acted_symbols: set[str] = set()
        allowed_symbols = {
            str(symbol)
            for symbol in self.config.get("candidate_symbols", [])
        }
        for symbol, position in list((self.state.get("positions") or {}).items()):
            reason: str | None = None
            if symbol not in allowed_symbols:
                reason = "Symbol nicht mehr im v3.9.3-Kandidatenpool"
            elif str(position.get("setup")) == "P":
                reason = "P-Setup seit v3.7.1 deaktiviert"
            if reason is None:
                continue
            signal = self.signals.get(symbol)
            self._funding_update(position, signal)
            self._close(
                position,
                _f(position["margin_usd"]),
                reason,
                130.0,
            )
            self.state.setdefault("cooldowns", {})[symbol] = int(
                self.now.timestamp()
            ) + int(self.config.get("paper_close_cooldown_minutes", 5)) * 60
            acted_symbols.add(symbol)

        for symbol in list((self.state.get("positions") or {}).keys()):
            position = self.state.get("positions", {}).get(symbol)
            if position is None:
                continue
            signal = self.signals.get(symbol)
            self._funding_update(position, signal)
            rows = list((snapshots.get(symbol) or {}).get("candles") or [])
            if rows and self._intrabar_action(position, rows):
                acted_symbols.add(symbol)
                continue
            if not fresh_decision:
                continue
            position = self.state.get("positions", {}).get(symbol)
            if position is None:
                continue
            if (
                signal is not None
                and _f(getattr(signal, "platform_max_leverage", 0.0))
                < int(position.get("leverage", 0))
            ):
                self._close(
                    position,
                    _f(position["margin_usd"]),
                    "Lighter-Markthebel wurde reduziert",
                    125.0,
                )
                self.state.setdefault("cooldowns", {})[symbol] = int(
                    self.now.timestamp()
                ) + int(self.config.get("paper_close_cooldown_minutes", 5)) * 60
                acted_symbols.add(symbol)
                continue
            if signal is not None and self._reverse_or_close(
                position,
                signal,
                ranked_index.get(symbol, 99),
            ):
                acted_symbols.add(symbol)
                continue
            position = self.state.get("positions", {}).get(symbol)
            if position is not None and self._signal_exit(position, signal):
                acted_symbols.add(symbol)

        if fresh_decision:
            for symbol, position in list((self.state.get("positions") or {}).items()):
                if symbol in acted_symbols:
                    continue
                signal = self.signals.get(symbol)
                if signal is not None:
                    self._maybe_add(position, signal, ranked_index.get(symbol, 99))

            entry_candidates = [
                signal
                for signal in signals
                if signal.state in ENTRY_STATES
                and signal.symbol not in self.state.get("positions", {})
                and signal.symbol not in acted_symbols
            ]
            primary_readiness: float | None = None
            max_positions = int(self.config.get("paper_max_positions", 3))
            for index, signal in enumerate(entry_candidates):
                if len(self.state.get("positions", {})) >= max_positions:
                    self._log(f"SKIP {signal.alias}: maximal {max_positions} Positionen")
                    continue
                rank = ranked_index.get(signal.symbol, index + 1)
                additional = len(self.state.get("positions", {})) > 0
                block = self._entry_block(signal, additional)
                if (
                    block is None
                    and additional
                    and (primary_readiness or float(signal.trade_readiness))
                    - float(signal.trade_readiness)
                    > float(self.config.get("paper_additional_max_readiness_gap", 6.0))
                ):
                    block = "zu großer Abstand zum besten Einstiegssignal"
                cooldown = int(
                    self.state.get("cooldowns", {}).get(signal.symbol, 0)
                )
                if block is None and cooldown > int(self.now.timestamp()):
                    block = "Symbol noch in Abkühlphase"
                stop_pct = self._planned_stop_pct(signal)
                leverage = self._leverage(signal, stop_pct)
                if block is None and leverage is None:
                    platform = _f(getattr(signal, "platform_max_leverage", 0.0))
                    block = f"Lighter-Maximalhebel {platform:g}x liegt unter 10x"
                if block:
                    self._log(
                        f"SKIP {signal.alias} {_direction_letter(ENTRY_STATES[signal.state])}: "
                        f"{block} | {signal.trade_readiness:.1f}/{signal.confidence:.1f}"
                    )
                    continue
                margin = self._target_margin(
                    signal,
                    len(self.state.get("positions", {})) + 1,
                )
                opened = self._open(
                    signal,
                    margin,
                    int(leverage),
                    rank,
                )
                if opened is not None and primary_readiness is None:
                    primary_readiness = float(signal.trade_readiness)

        self.state["run_count"] = int(self.state.get("run_count", 0)) + 1
        self.state["last_run_at"] = _iso(self.now)
        if fresh_decision:
            self.state["last_decision_key"] = decision_key
        if fresh_decision and not self.actions:
            top = " | ".join(
                f"{signal.alias} {signal.state} "
                f"{SETUP_CODES.get(signal.selected_setup, '+' if signal.direction >= 0 else '-')} "
                f"{signal.trade_readiness:.1f}/{signal.confidence:.1f}"
                for signal in signals[:3]
            )
            self._log(
                "NO TRADE: kein neues starkes und ausführbares Setup | "
                f"{top}"
            )
        equity, free, margin = self._equity()
        # Paper actions are deliberately log-only in v3.9.3.
        action_line = None
        self._log(
            f"KONTO Balance {_money(_f(self.state.get('balance_usd')), compact=False)} | "
            f"Equity {_money(equity, compact=False)} | "
            f"frei {_money(free, compact=False)} | "
            f"Margin {_money(margin, compact=False)} | "
            f"Positionen {len(self.state.get('positions', {}))}"
        )
        self._save_state()
        return {
            "action_line": action_line,
            "actions": [asdict(action) | {"token": action.token()} for action in self.actions],
            "logs": list(self.logs),
            "account": {
                "starting_balance_usd": _f(self.state.get("starting_balance_usd")),
                "balance_usd": _f(self.state.get("balance_usd")),
                "equity_usd": round(equity, 8),
                "free_margin_usd": round(free, 8),
                "used_margin_usd": round(margin, 8),
                "positions": len(self.state.get("positions", {})),
            },
            "positions": list(self.state.get("positions", {}).values()),
            "decision_key": decision_key,
            "fresh_decision": fresh_decision,
        }
