# r2
"""Persistent spontaneous-incident detection for CF v6.0.0.

Confirmed external SECURITY/NETWORK events and strict Lighter market shocks use
one state machine. Market evidence is labelled SHK! and never claims an exploit
cause. Incidents block fresh paper entries, own detail priority for ten minutes,
and reserve one alternative header slot for twenty-five minutes. BTC remains the
permanent right-hand macro/event anchor.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


STATE_VERSION = "incident-state-v600-r2"


@dataclass(frozen=True)
class IncidentMark:
    symbol: str
    code: str
    kind: str
    title: str
    starts_at: str
    ends_at: str
    priority: int
    risk: int
    active: bool
    block_new: bool
    leverage_cap: int
    source_name: str
    source_url: str
    confirmed: bool
    detected_at: str
    priority_until: str
    header_until: str
    danger_score: float
    fingerprint: str
    metrics: dict[str, Any]


@dataclass
class IncidentSnapshot:
    marks: dict[str, IncidentMark]
    display_codes: dict[str, str]
    priority_symbol: str | None
    header_symbol: str | None
    incidents: list[IncidentMark]
    diagnostics: list[str]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "priority_symbol": self.priority_symbol,
            "header_symbol": self.header_symbol,
            "marks": {symbol: asdict(mark) for symbol, mark in self.marks.items()},
            "incidents": [asdict(mark) for mark in self.incidents],
            "diagnostics": list(self.diagnostics),
        }


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    return int(_number(row.get("t", row.get("timestamp", 0))))


def _pct(start: float, end: float) -> float:
    return (end / start - 1.0) * 100.0 if start > 0 and end > 0 else 0.0


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "records": {}}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "records": {}}
    if not isinstance(raw.get("records"), dict):
        raw["records"] = {}
    return raw


def _save_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _contiguous(rows: list[Mapping[str, Any]]) -> bool:
    return all(
        _timestamp_ms(current) - _timestamp_ms(previous) == 60_000
        for previous, current in zip(rows, rows[1:])
    )


def _window_move(rows: list[Mapping[str, Any]], minutes: int) -> float | None:
    if len(rows) < minutes:
        return None
    sample = rows[-minutes:]
    opened = _number(sample[0].get("o"))
    closed = _number(sample[-1].get("c"))
    if opened <= 0 or closed <= 0:
        return None
    return _pct(opened, closed)


def _market_candidate(
    *,
    symbol: str,
    snapshot: Mapping[str, Any],
    btc_snapshot: Mapping[str, Any] | None,
    section: Mapping[str, Any],
    lighter_base_url: str,
) -> dict[str, Any] | None:
    raw_rows = snapshot.get("candles")
    if not isinstance(raw_rows, list):
        return None
    history = max(60, int(section.get("minimum_history_minutes", 90)))
    exclude = max(10, int(section.get("baseline_exclude_minutes", 10)))
    required = history + exclude
    if len(raw_rows) < required:
        return None
    rows = [row for row in raw_rows[-required:] if isinstance(row, Mapping)]
    if len(rows) != required or not _contiguous(rows):
        return None

    baseline = rows[:-exclude]
    recent = rows[-exclude:]
    prices = [_number(row.get("c")) for row in baseline]
    if not prices or min(prices) <= 0:
        return None
    returns = [
        abs(_pct(left, right))
        for left, right in zip(prices, prices[1:])
        if left > 0 and right > 0
    ]
    noise = max(0.015, statistics.median(returns) * 1.4826 if returns else 0.015)

    volumes = [max(0.0, _number(row.get("V"))) for row in baseline]
    positive = [value for value in volumes if value > 0]
    coverage = len(positive) / len(volumes) if volumes else 0.0
    minimum_coverage = max(
        0.0,
        min(1.0, float(section.get("minimum_baseline_coverage", 0.20))),
    )
    if coverage < minimum_coverage or not positive:
        return None
    sparse_threshold = max(
        minimum_coverage,
        min(1.0, float(section.get("sparse_coverage_threshold", 0.25))),
    )
    sparse = coverage < sparse_threshold
    sparse_multiplier = (
        min(
            max(1.0, float(section.get("sparse_volume_multiplier_cap", 3.0))),
            math.sqrt(sparse_threshold / max(coverage, 1e-9)),
        )
        if sparse
        else 1.0
    )
    positive.sort()
    cap = positive[min(len(positive) - 1, int(len(positive) * 0.90))]
    baseline_per_minute = statistics.fmean(min(value, cap) for value in volumes)
    if baseline_per_minute <= 0:
        return None

    btc_rows: list[Mapping[str, Any]] = []
    if btc_snapshot and isinstance(btc_snapshot.get("candles"), list):
        btc_rows = [
            row for row in btc_snapshot["candles"][-exclude:]
            if isinstance(row, Mapping)
        ]
        if len(btc_rows) < exclude or not _contiguous(btc_rows):
            btc_rows = []

    volume_thresholds = section.get("volume_ratio_thresholds") or {}
    price_floors = section.get("price_floor_pct") or {}
    noise_multipliers = section.get("noise_multipliers") or {}
    relative_fraction = max(0.0, float(section.get("relative_fraction", 0.65)))
    metrics: dict[str, Any] = {
        "noise_pct": round(noise, 6),
        "baseline_volume_per_minute": round(baseline_per_minute, 6),
        "baseline_volume_coverage": round(coverage, 4),
        "sparse_baseline": sparse,
        "sparse_volume_multiplier": round(sparse_multiplier, 4),
        "windows": {},
    }
    triggered: dict[int, bool] = {}
    for minutes in (1, 5, 10):
        move = _window_move(recent, minutes)
        if move is None:
            return None
        volume = sum(max(0.0, _number(row.get("V"))) for row in recent[-minutes:])
        volume_ratio = volume / (baseline_per_minute * minutes)
        threshold = max(
            float(price_floors.get(str(minutes), {1: 0.75, 5: 1.25, 10: 1.8}[minutes])),
            noise
            * math.sqrt(minutes)
            * float(noise_multipliers.get(str(minutes), {1: 7.0, 5: 5.0, 10: 4.0}[minutes])),
        )
        btc_move = _window_move(btc_rows, minutes) if btc_rows else None
        relative_move = move - btc_move if btc_move is not None else None
        relative_ok = (
            symbol == "BTC"
            or relative_move is None
            or abs(relative_move) >= threshold * relative_fraction
        )
        volume_threshold = float(
            volume_thresholds.get(str(minutes), {1: 10.0, 5: 5.0, 10: 4.0}[minutes])
        ) * sparse_multiplier
        triggered[minutes] = bool(
            abs(move) >= threshold
            and volume_ratio >= volume_threshold
            and relative_ok
        )
        metrics["windows"][str(minutes)] = {
            "move_pct": round(move, 6),
            "btc_move_pct": round(btc_move, 6) if btc_move is not None else None,
            "relative_move_pct": (
                round(relative_move, 6) if relative_move is not None else None
            ),
            "price_threshold_pct": round(threshold, 6),
            "volume_ratio": round(volume_ratio, 4),
            "volume_threshold": volume_threshold,
            "triggered": triggered[minutes],
        }

    one = metrics["windows"]["1"]
    one_strong = abs(one["move_pct"]) >= (
        one["price_threshold_pct"]
        * max(1.0, float(section.get("one_minute_multiplier", 1.35)))
    )
    acute = (
        triggered[5] and triggered[10]
        if sparse
        else (
            (triggered[1] and (one_strong or triggered[5]))
            or (triggered[5] and triggered[10])
        )
    )
    if not acute:
        return None

    decisive_minutes = next(
        (minutes for minutes in (1, 5, 10) if triggered[minutes]),
        10,
    )
    decisive = metrics["windows"][str(decisive_minutes)]
    direction = "UP" if decisive["move_pct"] > 0 else "DOWN"
    price_factor = abs(decisive["move_pct"]) / max(
        decisive["price_threshold_pct"], 1e-9
    )
    volume_factor = decisive["volume_ratio"] / max(
        decisive["volume_threshold"], 1e-9
    )
    danger = min(99.0, 88.0 + min(6.0, (price_factor - 1.0) * 4.0) + min(5.0, (volume_factor - 1.0) * 3.0))
    return {
        "symbol": symbol,
        "code": str(section.get("market_shock_code", "SHK!")),
        "kind": "MARKET_SHOCK",
        "title": f"Coin-specific {direction.lower()} market shock; cause unverified",
        "priority": 98,
        "risk": 100,
        "confirmed": False,
        "danger_score": danger,
        "fingerprint": f"MARKET_SHOCK|{symbol}|{direction}",
        "source_name": "Lighter market evidence",
        "source_url": lighter_base_url.rstrip("/") + "/candles",
        "metrics": metrics,
    }


def _external_candidates(
    marks: Mapping[str, Any],
    section: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    acute_kinds = {
        str(value).upper().strip()
        for value in section.get("acute_kinds", ["SECURITY", "NETWORK"])
        if str(value).strip()
    }
    min_priority = int(section.get("external_min_priority", 98))
    min_risk = int(section.get("external_min_risk", 90))
    result: dict[str, dict[str, Any]] = {}
    for raw_symbol, mark in marks.items():
        symbol = str(raw_symbol).upper().strip()
        kind = str(_value(mark, "kind", "")).upper().strip()
        priority = int(_number(_value(mark, "priority", 0)))
        risk = int(_number(_value(mark, "risk", 0)))
        active = bool(_value(mark, "active", False))
        block_new = bool(_value(mark, "block_new", False))
        if (
            not symbol
            or kind not in acute_kinds
            or not (active or block_new)
            or priority < min_priority
            or risk < min_risk
        ):
            continue
        title = str(_value(mark, "title", kind) or kind)
        starts = str(_value(mark, "starts_at", "") or "")
        source_url = str(_value(mark, "source_url", "") or "")
        result[symbol] = {
            "symbol": symbol,
            "code": str(_value(mark, "code", "SEC!") or "SEC!"),
            "kind": kind,
            "title": title,
            "priority": priority,
            "risk": risk,
            "confirmed": True,
            "danger_score": float(max(priority, risk)),
            "fingerprint": "|".join((kind, symbol, starts, title, source_url)),
            "source_name": str(_value(mark, "source_name", "Verified event feed") or "Verified event feed"),
            "source_url": source_url,
            "metrics": {},
        }
    return result


def _new_record(
    candidate: Mapping[str, Any],
    now: datetime,
    priority_minutes: int,
    header_minutes: int,
) -> dict[str, Any]:
    return {
        **dict(candidate),
        "detected_at": _iso(now),
        "last_seen_at": _iso(now),
        "priority_until": _iso(now + timedelta(minutes=priority_minutes)),
        "header_until": _iso(now + timedelta(minutes=header_minutes)),
        "was_triggering": True,
        "cleared_at": None,
    }


def detect_spontaneous_incidents(
    config: Mapping[str, Any],
    *,
    signals: list[Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    event_marks: Mapping[str, Any] | None,
    now: datetime,
    state_path: Path | None,
) -> IncidentSnapshot:
    section = config.get("incident_detection")
    section = section if isinstance(section, Mapping) else {}
    if not bool(section.get("enabled", True)):
        return IncidentSnapshot({}, {}, None, None, [], [], _iso(now))

    priority_minutes = max(1, int(section.get("priority_minutes", 10)))
    header_minutes = max(
        priority_minutes,
        int(section.get("header_priority_minutes", 25)),
    )
    retention_minutes = max(
        header_minutes,
        int(section.get("state_retention_minutes", 120)),
    )
    reset_cooldown = max(1, int(section.get("reset_cooldown_minutes", 5)))
    diagnostics: list[str] = []
    external = _external_candidates(event_marks or {}, section)
    candidates = dict(external)
    btc_snapshot = snapshots.get("BTC")
    allowed = {str(value).upper() for value in config.get("candidate_symbols", [])}
    lighter_base_url = str(config.get("lighter_base_url", ""))
    for symbol in sorted(allowed):
        if symbol in external:
            continue
        snapshot = snapshots.get(symbol)
        if not isinstance(snapshot, Mapping):
            continue
        candidate = _market_candidate(
            symbol=symbol,
            snapshot=snapshot,
            btc_snapshot=btc_snapshot,
            section=section,
            lighter_base_url=lighter_base_url,
        )
        if candidate is not None:
            candidates[symbol] = candidate

    state = (
        _load_state(state_path)
        if state_path is not None
        else {"version": STATE_VERSION, "records": {}}
    )
    old_records = state.get("records") or {}
    records: dict[str, dict[str, Any]] = {
        str(symbol): dict(record)
        for symbol, record in old_records.items()
        if isinstance(record, Mapping)
    }

    now_s = now.timestamp()
    for symbol, candidate in candidates.items():
        existing = records.get(symbol)
        same = bool(
            existing
            and str(existing.get("fingerprint")) == str(candidate.get("fingerprint"))
        )
        cleared = _parse_iso(existing.get("cleared_at")) if existing else None
        cooldown_active = bool(
            cleared and now_s - cleared.timestamp() < reset_cooldown * 60
        )
        if same and (bool(existing.get("was_triggering")) or cooldown_active):
            record = dict(existing)
            for key, value in candidate.items():
                if key not in {"detected_at", "priority_until", "header_until"}:
                    record[key] = value
            record["last_seen_at"] = _iso(now)
            record["was_triggering"] = True
            record["cleared_at"] = None
        else:
            record = _new_record(
                candidate,
                now,
                priority_minutes,
                header_minutes,
            )
        records[symbol] = record

    for symbol, record in list(records.items()):
        if symbol not in candidates and bool(record.get("was_triggering")):
            record["was_triggering"] = False
            record["cleared_at"] = _iso(now)
        last_seen = _parse_iso(record.get("last_seen_at"))
        header_until = _parse_iso(record.get("header_until"))
        if last_seen is None or (
            now_s - last_seen.timestamp() > retention_minutes * 60
            and (header_until is None or now > header_until)
        ):
            records.pop(symbol, None)

    marks: dict[str, IncidentMark] = {}
    active_records: list[dict[str, Any]] = []
    for symbol, record in records.items():
        priority_until = _parse_iso(record.get("priority_until"))
        header_until = _parse_iso(record.get("header_until"))
        detected_at = _parse_iso(record.get("detected_at"))
        still_triggering = bool(record.get("was_triggering"))
        if (
            priority_until is None
            or header_until is None
            or detected_at is None
            or (now > header_until and not still_triggering)
        ):
            continue
        active_records.append(record)
        kind = str(record.get("kind") or "MARKET_SHOCK")
        confirmed = bool(record.get("confirmed"))
        # A market-only SHK! remains visible for the full header window, but
        # once the acute phase has elapsed and the shock is no longer firing,
        # a structurally confirmed rebound/turnaround may be evaluated again.
        # Confirmed SECURITY/NETWORK incidents remain hard blocks.
        market_shock = kind == "MARKET_SHOCK" and not confirmed
        acute_market_shock = market_shock and (still_triggering or now <= priority_until)
        hard_active = acute_market_shock if market_shock else True
        hard_block = acute_market_shock if market_shock else True
        residual_risk = int(record.get("risk") or 100)
        if market_shock and not hard_active:
            residual_risk = min(residual_risk, 45)
        mark = IncidentMark(
            symbol=symbol,
            code=str(record.get("code") or "SHK!"),
            kind=kind,
            title=str(record.get("title") or "Spontaneous incident"),
            starts_at=_iso(detected_at),
            ends_at=_iso(header_until),
            priority=int(record.get("priority") or 98),
            risk=residual_risk,
            active=hard_active,
            block_new=hard_block,
            leverage_cap=1 if hard_block else None,
            source_name=str(record.get("source_name") or "Lighter market evidence"),
            source_url=str(record.get("source_url") or lighter_base_url),
            confirmed=confirmed,
            detected_at=_iso(detected_at),
            priority_until=_iso(priority_until),
            header_until=_iso(header_until),
            danger_score=float(record.get("danger_score") or 0.0),
            fingerprint=str(record.get("fingerprint") or ""),
            metrics=dict(record.get("metrics") or {}),
        )
        marks[symbol] = mark

    def rank(record: Mapping[str, Any]) -> tuple[int, float, float]:
        detected = _parse_iso(record.get("detected_at"))
        return (
            1 if bool(record.get("confirmed")) else 0,
            float(record.get("danger_score") or 0.0),
            detected.timestamp() if detected else 0.0,
        )

    priority_candidates = [
        record
        for record in active_records
        if (_parse_iso(record.get("priority_until")) or now - timedelta(seconds=1)) >= now
    ]
    header_candidates = [
        record
        for record in active_records
        if (_parse_iso(record.get("header_until")) or now - timedelta(seconds=1)) >= now
    ]
    priority_record = max(priority_candidates, key=rank) if priority_candidates else None
    header_record = max(header_candidates, key=rank) if header_candidates else None

    if state_path is not None:
        try:
            _save_state(
                state_path,
                {
                    "version": STATE_VERSION,
                    "updated_at": _iso(now),
                    "records": records,
                },
            )
        except OSError as exc:
            diagnostics.append(f"Incident-State nicht gespeichert: {exc}")

    incident_marks = sorted(marks.values(), key=lambda item: (item.confirmed, item.danger_score), reverse=True)
    return IncidentSnapshot(
        marks=marks,
        display_codes={symbol: mark.code for symbol, mark in marks.items()},
        priority_symbol=str(priority_record.get("symbol")) if priority_record else None,
        header_symbol=str(header_record.get("symbol")) if header_record else None,
        incidents=incident_marks,
        diagnostics=diagnostics,
        generated_at=_iso(now),
    )


