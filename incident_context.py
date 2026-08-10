# r4
"""Acute incident protection for CF v7.0.0.

A MARKET_SHOCK is price evidence only: a statistically unusual, strongly
one-sided 15-minute displacement.  It never claims an exploit/news cause.
While the move remains one-sided, SHK! is forced into the top radar and the
symbol is blocked from lower trade lines until the move clears.  The block
ends when a meaningful counter-move starts or the move demonstrably calms.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

STATE_VERSION = "incident-state-v700-r1"


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
    leverage_cap: int | None
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
    return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _ts(row: Mapping[str, Any]) -> int:
    raw = int(_number(row.get("t"), _number(row.get("timestamp"))))
    return raw * 1000 if 0 < raw < 10_000_000_000 else raw


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


def _clean(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_time: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        stamp = _ts(row)
        close = _number(row.get("c"))
        if stamp > 0 and close > 0:
            by_time[stamp] = row
    return [by_time[key] for key in sorted(by_time)]


def _contiguous(rows: Sequence[Mapping[str, Any]]) -> bool:
    rows = list(rows)
    return bool(rows) and all(_ts(b) - _ts(a) == 60_000 for a, b in zip(rows, rows[1:]))


def _median_mad(values: Sequence[float]) -> tuple[float, float]:
    vals = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return 0.0, 0.0
    median = statistics.median(vals)
    mad = statistics.median(abs(v - median) for v in vals)
    return median, mad


def _load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "records": {}}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION or not isinstance(raw.get("records"), dict):
        return {"version": STATE_VERSION, "records": {}}
    return raw


def _save_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _market_candidate(
    *,
    symbol: str,
    snapshot: Mapping[str, Any],
    section: Mapping[str, Any],
    lighter_base_url: str,
) -> dict[str, Any] | None:
    rows = _clean(list(snapshot.get("candles") or []))
    recent_minutes = 15
    baseline_minutes = max(120, int(section.get("shock_baseline_minutes", 180)))
    # Exactly 15 CLOSED one-minute candles form the acute window. Keep the
    # historical baseline disjoint so the first shock candle can never inflate
    # its own comparison threshold.
    required = baseline_minutes + recent_minutes
    if len(rows) < required:
        return None
    rows = rows[-required:]
    if not _contiguous(rows):
        return None

    recent = rows[-recent_minutes:]
    baseline = rows[:-recent_minutes]
    closes = [_number(row.get("c")) for row in recent]
    opens = [_number(row.get("o"), _number(row.get("c"))) for row in recent]
    if min(closes, default=0.0) <= 0 or min(opens, default=0.0) <= 0:
        return None

    start_price = opens[0]
    end_price = closes[-1]
    move = _pct(start_price, end_price)
    if abs(move) <= 1e-12:
        return None
    direction = 1 if move > 0 else -1

    # Use the actual 15m path from the first candle open through every close.
    # Omitting the first open→close leg would undercount path length and could
    # overstate one-sided efficiency when the first minute itself is the shock.
    path_prices = [start_price, *closes]
    minute_returns = [_pct(a, b) for a, b in zip(path_prices, path_prices[1:])]
    travelled = sum(abs(v) for v in minute_returns)
    efficiency = _clamp(abs(move) / max(travelled, 1e-9), 0.0, 1.0)

    highs = [max(_number(row.get("h"), close), close) for row, close in zip(recent, closes)]
    lows = [min(_number(row.get("l"), close), close) for row, close in zip(recent, closes)]
    extreme = max(highs) if direction > 0 else min(lows)
    full_excursion = abs(_pct(start_price, extreme))
    retrace = abs(_pct(extreme, end_price)) / max(full_excursion, 1e-9)

    base_closes = [_number(row.get("c")) for row in baseline]
    one_min_abs = [abs(_pct(a, b)) for a, b in zip(base_closes, base_closes[1:])]
    noise_med, noise_mad = _median_mad(one_min_abs)
    robust_noise = max(0.01, noise_med + 1.4826 * noise_mad)
    # Flat/minuscule minutes must not make a truly abrupt one-way jump look
    # "two-sided" merely because only one of 15 bars carried the move. Judge
    # direction consistency on movement that is meaningful relative to the
    # coin's own robust baseline noise. Reversal legs remain fully counted.
    direction_floor = max(0.003, robust_noise * 0.35)
    directional_returns = [value for value in minute_returns if abs(value) >= direction_floor]
    same_direction = (
        sum(1 for value in directional_returns if value * direction > 0)
        / max(1, len(directional_returns))
    )
    historic_15m: list[float] = []
    for end in range(15, len(base_closes), 5):
        historic_15m.append(abs(_pct(base_closes[end - 15], base_closes[end])))
    hist_med, hist_mad = _median_mad(historic_15m)

    floor = float(section.get("shock_15m_floor_pct", 0.90))
    threshold = max(
        floor,
        hist_med + float(section.get("shock_robust_mad_multiple", 5.0)) * 1.4826 * hist_mad,
        robust_noise * math.sqrt(15.0) * float(section.get("shock_noise_multiple", 4.5)),
    )
    min_eff = float(section.get("shock_min_path_efficiency", 0.78))
    min_same = float(section.get("shock_min_same_direction_fraction", 0.72))
    max_retrace = float(section.get("shock_max_retrace_fraction", 0.22))
    if abs(move) < threshold or efficiency < min_eff or same_direction < min_same or retrace > max_retrace:
        return None

    excess = abs(move) / max(threshold, 1e-9)
    one_sided = 0.55 * efficiency + 0.30 * same_direction + 0.15 * (1.0 - min(1.0, retrace))
    danger = min(99.0, 82.0 + min(11.0, max(0.0, excess - 1.0) * 8.0) + min(6.0, max(0.0, one_sided - 0.75) * 24.0))
    metrics = {
        "window_minutes": 15,
        "direction": direction,
        "move_pct": round(move, 6),
        "threshold_pct": round(threshold, 6),
        "path_efficiency": round(efficiency, 6),
        "same_direction_fraction": round(same_direction, 6),
        "retrace_fraction": round(retrace, 6),
        "start_price": round(start_price, 12),
        "extreme_price": round(extreme, 12),
        "end_price": round(end_price, 12),
        "robust_noise_pct": round(robust_noise, 6),
        "historic_15m_median_pct": round(hist_med, 6),
        "historic_15m_mad_pct": round(hist_mad, 6),
    }
    direction_text = "up" if direction > 0 else "down"
    return {
        "symbol": symbol,
        "code": str(section.get("market_shock_code", "SHK!")),
        "kind": "MARKET_SHOCK",
        "title": f"one-sided 15m {direction_text} shock; cause unverified",
        "priority": 99,
        "risk": int(round(danger)),
        "confirmed": False,
        "danger_score": danger,
        "fingerprint": f"MARKET_SHOCK|{symbol}|{direction}",
        "source_name": "Lighter 1m candles",
        "source_url": lighter_base_url.rstrip("/") + "/candles",
        "metrics": metrics,
    }


def _shock_has_calmed_or_reversed(
    record: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    section: Mapping[str, Any],
) -> bool:
    if not isinstance(snapshot, Mapping):
        return False
    rows = _clean(list(snapshot.get("candles") or []))
    if len(rows) < 7 or not _contiguous(rows[-7:]):
        return False
    metrics = record.get("metrics") if isinstance(record.get("metrics"), Mapping) else {}
    direction = int(_number(metrics.get("direction")))
    start = _number(metrics.get("start_price"))
    extreme = _number(metrics.get("extreme_price"))
    threshold = abs(_number(metrics.get("threshold_pct")))
    noise = max(0.01, abs(_number(metrics.get("robust_noise_pct"))))
    if direction not in {-1, 1} or start <= 0 or extreme <= 0:
        return False

    current = _number(rows[-1].get("c"))
    excursion = abs(_pct(start, extreme))
    retrace_from_extreme = abs(_pct(extreme, current)) / max(excursion, 1e-9)
    reversal_fraction = float(section.get("shock_clear_retrace_fraction", 0.28))
    if retrace_from_extreme >= reversal_fraction:
        return True

    closes = [_number(row.get("c")) for row in rows[-6:]]
    returns = [_pct(a, b) for a, b in zip(closes, closes[1:])]
    opposite_count = sum(1 for value in returns if value * direction < 0)
    net_opposite = -_pct(closes[0], closes[-1]) * direction
    if (
        opposite_count >= int(section.get("shock_clear_opposite_closes", 3))
        and net_opposite >= max(noise * 2.5, threshold * float(section.get("shock_clear_countermove_fraction", 0.16)))
    ):
        return True

    # Calm = no meaningful continuation and tiny five-minute displacement after
    # the extreme phase. This does not require an opposite trade signal.
    last5_abs = abs(_pct(closes[0], closes[-1]))
    recent_high = max(_number(row.get("h"), _number(row.get("c"))) for row in rows[-6:])
    recent_low = min(_number(row.get("l"), _number(row.get("c"))) for row in rows[-6:])
    new_extreme = recent_high > extreme if direction > 0 else recent_low < extreme
    calm_limit = max(noise * 2.2, threshold * float(section.get("shock_calm_5m_fraction", 0.10)))
    return (not new_extreme) and last5_abs <= calm_limit


def _external_candidates(marks: Mapping[str, Any], section: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    acute_kinds = {str(v).upper().strip() for v in section.get("acute_kinds", ["SECURITY", "NETWORK"]) if str(v).strip()}
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
        if not symbol or kind not in acute_kinds or not (active or block_new) or priority < min_priority or risk < min_risk:
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


def detect_spontaneous_incidents(
    config: Mapping[str, Any],
    *,
    signals: list[Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    event_marks: Mapping[str, Any] | None,
    now: datetime,
    state_path: Path | None,
) -> IncidentSnapshot:
    del signals
    section = config.get("incident_detection")
    section = section if isinstance(section, Mapping) else {}
    if not bool(section.get("enabled", True)):
        return IncidentSnapshot({}, {}, None, None, [], [], _iso(now))

    retention = max(30, int(section.get("state_retention_minutes", 120)))
    max_missing = max(2, int(section.get("shock_missing_data_grace_minutes", 5)))
    lighter_base_url = str(config.get("lighter_base_url", ""))
    allowed = {str(v).upper() for v in config.get("candidate_symbols", [])}
    external = _external_candidates(event_marks or {}, section)
    market: dict[str, dict[str, Any]] = {}
    for symbol in sorted(allowed):
        if symbol in external:
            continue
        snapshot = snapshots.get(symbol)
        if isinstance(snapshot, Mapping):
            candidate = _market_candidate(symbol=symbol, snapshot=snapshot, section=section, lighter_base_url=lighter_base_url)
            if candidate:
                market[symbol] = candidate

    state = _load_state(state_path) if state_path is not None else {"version": STATE_VERSION, "records": {}}
    records = {str(k): dict(v) for k, v in (state.get("records") or {}).items() if isinstance(v, Mapping)}
    now_ts = now.timestamp()

    # A previously detected one-sided shock must be allowed to clear as soon as
    # a real counter-move/reversal begins, even while the full trailing 15m net
    # displacement is still large enough to satisfy the raw shock detector.
    # Otherwise the same-direction candidate would refresh forever and the clear
    # logic below would never get a chance to run.
    for symbol, candidate in list(market.items()):
        existing = records.get(symbol)
        if (
            isinstance(existing, Mapping)
            and existing.get("fingerprint") == candidate.get("fingerprint")
            and _shock_has_calmed_or_reversed(existing, snapshots.get(symbol), section)
        ):
            market.pop(symbol, None)

    candidates = {**external, **market}

    for symbol, candidate in candidates.items():
        existing = records.get(symbol)
        same = bool(existing and existing.get("fingerprint") == candidate.get("fingerprint"))
        detected = str(existing.get("detected_at")) if same and existing else _iso(now)
        records[symbol] = {
            **(dict(existing) if same and existing else {}),
            **candidate,
            "detected_at": detected,
            "last_seen_at": _iso(now),
            "was_triggering": True,
            "cleared_at": None,
        }

    for symbol, record in list(records.items()):
        if symbol in candidates:
            continue
        kind = str(record.get("kind") or "")
        if bool(record.get("was_triggering")):
            if kind == "MARKET_SHOCK" and not bool(record.get("confirmed")):
                if _shock_has_calmed_or_reversed(record, snapshots.get(symbol), section):
                    record["was_triggering"] = False
                    record["cleared_at"] = _iso(now)
                else:
                    last_seen = _parse_iso(record.get("last_seen_at"))
                    # If fresh valid candles say neither clear nor retrigger, retain
                    # the block. With missing data, fail safe only for a short grace;
                    # the signal itself will be INVALID_DATA afterwards.
                    if last_seen and now_ts - last_seen.timestamp() > max_missing * 60:
                        record["was_triggering"] = False
                        record["cleared_at"] = _iso(now)
            else:
                record["was_triggering"] = False
                record["cleared_at"] = _iso(now)
        last_seen = _parse_iso(record.get("last_seen_at"))
        if last_seen and now_ts - last_seen.timestamp() > retention * 60:
            records.pop(symbol, None)

    marks: dict[str, IncidentMark] = {}
    for symbol, record in records.items():
        if not bool(record.get("was_triggering")):
            continue
        detected = _parse_iso(record.get("detected_at")) or now
        last_seen = _parse_iso(record.get("last_seen_at")) or now
        kind = str(record.get("kind") or "MARKET_SHOCK")
        confirmed = bool(record.get("confirmed"))
        end = now + timedelta(minutes=1)
        marks[symbol] = IncidentMark(
            symbol=symbol,
            code=str(record.get("code") or "SHK!"),
            kind=kind,
            title=str(record.get("title") or "Acute incident"),
            starts_at=_iso(detected),
            ends_at=_iso(end),
            priority=int(record.get("priority") or 99),
            risk=int(record.get("risk") or 99),
            active=True,
            block_new=True,
            leverage_cap=1,
            source_name=str(record.get("source_name") or "Lighter 1m candles"),
            source_url=str(record.get("source_url") or lighter_base_url),
            confirmed=confirmed,
            detected_at=_iso(detected),
            priority_until=_iso(end),
            header_until=_iso(end),
            danger_score=float(record.get("danger_score") or 99.0),
            fingerprint=str(record.get("fingerprint") or ""),
            metrics=dict(record.get("metrics") or {}),
        )

    ordered = sorted(marks.values(), key=lambda m: (m.confirmed, m.danger_score, m.priority, m.detected_at), reverse=True)
    priority_symbol = ordered[0].symbol if ordered else None
    if state_path is not None:
        _save_state(state_path, {"version": STATE_VERSION, "updated_at": _iso(now), "records": records})
    return IncidentSnapshot(
        marks=marks,
        display_codes={symbol: mark.code for symbol, mark in marks.items()},
        priority_symbol=priority_symbol,
        header_symbol=priority_symbol,
        incidents=ordered,
        diagnostics=[],
        generated_at=_iso(now),
    )
