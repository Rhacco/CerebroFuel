# r2
"""Cautious, re-alertable paper-trade diagnostics for CF v7.0.0.

No finding changes parameters automatically.  Findings are evidence summaries
and concrete things to inspect; they are never labelled as statistical proof.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable, Mapping

STATE_VERSION = "paper-optimizer-v700-r1"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Finding:
    key: str
    label: str
    samples: int
    average_r: float
    evidence: str
    recommendation: str
    level: str


def _completed_trades(paper_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def append_close(action: Mapping[str, Any], details: Mapping[str, Any], timestamp: str) -> None:
        if not bool(details.get("full_close", False)):
            return
        features = details.get("entry_features")
        if not isinstance(features, Mapping) or features.get("optimizer_compatible") is False:
            return
        # v7 deliberately evaluates the total risk ever committed to the trade;
        # partial closes do not shrink this denominator and scale-ins increase it.
        risk = _f(features.get("risk_committed_usd"))
        if risk <= 1e-9:
            return
        pnl = _f(details.get("trade_net_pnl_usd"), _f(action.get("realized_pnl_usd")))
        rows.append({
            "symbol": str(action.get("symbol") or ""),
            "pnl": pnl,
            "r": pnl / risk,
            "win": pnl > 0,
            "features": dict(features),
            "reason": str(action.get("reason") or ""),
            "timestamp": timestamp,
            "holding_minutes": _f(details.get("holding_minutes")),
        })

    for entry in paper_state.get("ledger") or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        details = entry.get("details")
        if not isinstance(action, Mapping) or not isinstance(details, Mapping):
            continue
        timestamp = str(entry.get("timestamp") or "")
        kind = str(action.get("kind") or "")
        if kind == "CLOSE":
            append_close(action, details, timestamp)
        elif kind == "REVERSE" and isinstance(details.get("reverse_close"), Mapping):
            append_close(action, details["reverse_close"], timestamp)
    return sorted(rows, key=lambda row: str(row.get("timestamp") or ""))


def _stats(rows: Iterable[Mapping[str, Any]]) -> tuple[int, float, float]:
    data = list(rows)
    if not data:
        return 0, 0.0, 0.0
    return len(data), sum(bool(row.get("win")) for row in data) / len(data), mean(_f(row.get("r")) for row in data)


def _rapid_rules(config: Mapping[str, Any]) -> list[tuple[str, str, str, Callable[[Mapping[str, Any]], bool]]]:
    stop_minutes = float(config.get("paper_optimizer_rapid_stop_minutes", 4.0))
    return [
        ("IMMEDIATE_STOP", "sehr schneller Stop", "prüfen: Einstiegstiming/NEAR→TRY-Bestätigung strenger machen, nicht automatisch ändern", lambda row: _f(row.get("holding_minutes")) <= stop_minutes and _f(row.get("r")) < 0 and ("stop" in str(row.get("reason") or "").lower() or _f(row.get("r")) <= -0.50)),
        ("EXTREME_CHASE", "Einstieg in Überdehnung", "prüfen: Extremity-Chase-Grenzen früher greifen lassen", lambda row: bool(row["features"].get("extremity_available", False)) and _f(row["features"].get("extremity_score")) * int(_f(row["features"].get("direction"))) >= 55.0),
        ("RELATIVE_OPPOSITION", "W gegen relative Marktteilnahme", "prüfen: W-Reclaim/relative Bestätigung verschärfen", lambda row: bool(row["features"].get("reversal_relative_opposition", False))),
        ("LATE_ENTRY", "E zu spät/zu weit verbraucht", "prüfen: E-Alter oder verbrauchten Bewegungsanteil reduzieren", lambda row: str(row["features"].get("setup") or "") == "E" and (_f(row["features"].get("setup_age_minutes")) > float(config.get("paper_early_max_age_minutes", 2)) or _f(row["features"].get("setup_consumed_fraction")) > float(config.get("paper_early_max_consumed_fraction", 0.66)))),
        ("COST_HEAVY", "Kosten relativ zum Stop hoch", "prüfen: maximale Roundtrip-Kosten/Ordergröße senken", lambda row: _f(row["features"].get("stop_pct")) > 0 and _f(row["features"].get("cost_pct")) / _f(row["features"].get("stop_pct")) >= 0.35),
        ("EVENT_RISK", "Ereignisrisiko beim Einstieg hoch", "prüfen: bei hohem E-Score Margin/Hebel früher begrenzen", lambda row: bool(row["features"].get("event_score_available", True)) and _f(row["features"].get("event_risk")) >= 55),
    ]


def _rapid_findings(trades: list[dict[str, Any]], config: Mapping[str, Any]) -> list[Finding]:
    if not bool(config.get("paper_optimizer_rapid_enabled", True)) or not trades:
        return []
    recent = trades[-max(1, min(3, int(config.get("paper_optimizer_rapid_max_trades", 3)))):]
    loss_threshold = float(config.get("paper_optimizer_rapid_loss_r", -0.65))
    findings: list[Finding] = []
    for key, label, recommendation, predicate in _rapid_rules(config):
        affected = [row for row in recent if predicate(row)]
        if not affected:
            continue
        avg_r = mean(_f(row.get("r")) for row in affected)
        severe_single = len(affected) == 1 and avg_r <= min(loss_threshold, -0.80)
        repeated = len(affected) >= 2 and avg_r <= max(loss_threshold, -0.35)
        if not (severe_single or repeated):
            continue
        symbols = ",".join(sorted({str(row.get("symbol") or "?") for row in affected}))
        findings.append(Finding(
            key="RAPID_" + key,
            label=label,
            samples=len(affected),
            average_r=round(avg_r, 4),
            evidence=f"{label}: n={len(affected)} ({symbols}), ØR {avg_r:+.2f}; früher Diagnosehinweis, keine belastbare Parameterbestätigung",
            recommendation=recommendation,
            level="rapid",
        ))
    return findings


def _feature_rules(config: Mapping[str, Any]) -> list[tuple[str, str, str, Callable[[Mapping[str, Any]], bool]]]:
    j_high = float(config.get("paper_springer_high_score", 78.0))
    j_extreme = float(config.get("paper_springer_extreme_score", 92.0))
    j_low = float(config.get("paper_springer_low_score", 28.0))
    return [
        ("LOW_DATA", "niedrige Datenqualität", "prüfen: Datenqualitäts-Gate erhöhen", lambda f: _f(f.get("data_quality"), 100) < 75),
        ("LOW_READINESS", "Readiness unter 66", "prüfen: NEAR/TRY/NOW-Schwellen gegen Episoden-Outcomes vergleichen", lambda f: _f(f.get("readiness"), 100) < 66),
        ("LOW_CONFIDENCE", "Confidence unter 62", "prüfen: Confidence-Untergrenze anheben", lambda f: _f(f.get("confidence"), 100) < 62),
        ("LOW_BTC", "niedriger BTC-Kontext", "prüfen: BTC-Gegenwind stärker bestrafen", lambda f: _f(f.get("btc_context"), 58) < 42),
        ("LOW_VOLUME", "schwache Volumenbestätigung", "prüfen: Volumenbestätigung anheben", lambda f: _f(f.get("volume_confirmation")) < 50),
        ("LOW_TAPE", "niedrige Tape-Qualität", "prüfen: Tape-Gate erhöhen", lambda f: _f(f.get("tape_quality")) < 70),
        ("HIGH_COST", "hohe Roundtrip-Kosten", "prüfen: Kostenlimit/Positionsgröße reduzieren", lambda f: _f(f.get("cost_pct")) > 0.065),
        ("REGIME_AGAINST", "7/14/30D-Regime gegen Einstieg", "prüfen: Gegenregime-Abschlag erhöhen", lambda f: bool(f.get("regime_available", False)) and _f(f.get("regime_modifier")) <= -4),
        ("EXTREME_CHASE", "Einstieg in Richtung einer Überdehnung", "prüfen: Extremity-Warn-/Blockgrenze senken", lambda f: bool(f.get("extremity_available", False)) and _f(f.get("extremity_score")) * int(_f(f.get("direction"))) >= 45),
        ("J_EXTREME", "extreme normale Springer-Stärke", "prüfen: bei J-extrem Hebel/Margin weiter begrenzen, falls Netto-R schwach bleibt", lambda f: bool(f.get("springer_available", False)) and _f(f.get("springer_score")) >= j_extreme),
        ("J_HIGH", "hohe normale Springer-Stärke", "prüfen: J-hoch nur bei positiver Netto-Erwartung größer handeln", lambda f: bool(f.get("springer_available", False)) and j_high <= _f(f.get("springer_score")) < j_extreme),
        ("J_LOW", "niedrige normale Springer-Stärke", "prüfen: J-niedrig seltener/scout-kleiner handeln", lambda f: bool(f.get("springer_available", False)) and _f(f.get("springer_score")) < j_low),
        ("EVENT_RISK", "erhöhtes Ereignisrisiko", "prüfen: E-Score-Sizing/Gate verschärfen", lambda f: bool(f.get("event_score_available", True)) and _f(f.get("event_risk")) >= 45),
        ("CLASS_A", "Springer-Klasse A", "Klasse A separat gegen B/C vergleichen", lambda f: str(f.get("springer_class")) == "A"),
        ("CLASS_B", "Springer-Klasse B", "Klasse B separat gegen A/C vergleichen", lambda f: str(f.get("springer_class")) == "B"),
        ("CLASS_C", "Springer-Klasse C", "Klasse C separat gegen A/B vergleichen", lambda f: str(f.get("springer_class")) == "C"),
        ("SETUP_T", "T-Setup", "T-Setup separat nach Tier/Richtung prüfen", lambda f: str(f.get("setup")) == "T"),
        ("SETUP_E", "E-Setup", "E-Setup separat nach Frische/Extremity prüfen", lambda f: str(f.get("setup")) == "E"),
        ("SETUP_W", "W-Setup", "W-Setup separat nach Reclaim/Extremity prüfen", lambda f: str(f.get("setup")) == "W"),
    ]


def _comparative_findings(trades: list[dict[str, Any]], config: Mapping[str, Any]) -> list[Finding]:
    minimum_total = max(8, int(config.get("paper_optimizer_min_total_trades", 8)))
    minimum_bucket = max(3, int(config.get("paper_optimizer_min_bucket_trades", 3)))
    minimum_gap_r = max(0.15, float(config.get("paper_optimizer_min_r_gap", 0.25)))
    maximum_bucket_win_rate = min(0.5, float(config.get("paper_optimizer_max_bucket_win_rate", 0.5)))
    if len(trades) < minimum_total:
        return []
    findings: list[Finding] = []
    for key, label, recommendation, predicate in _feature_rules(config):
        bucket = [row for row in trades if predicate(row["features"])]
        other = [row for row in trades if not predicate(row["features"])]
        n, win, avg_r = _stats(bucket)
        other_n, other_win, other_avg_r = _stats(other)
        if n < minimum_bucket or other_n < minimum_bucket:
            continue
        symbol_count = len({str(row.get("symbol") or "") for row in bucket if row.get("symbol")})
        if symbol_count < 2 or win > maximum_bucket_win_rate or avg_r >= -0.05 or other_avg_r - avg_r < minimum_gap_r:
            continue
        findings.append(Finding(
            key="COMP_" + key,
            label=label,
            samples=n,
            average_r=round(avg_r, 4),
            evidence=(f"{label}: n={n}/{symbol_count} Coins, Treffer {win:.0%}, ØR {avg_r:+.2f}; Vergleich n={other_n}, Treffer {other_win:.0%}, ØR {other_avg_r:+.2f}; heuristischer Vergleich, keine Signifikanz-/OOS-Bestätigung"),
            recommendation=recommendation,
            level="comparative",
        ))
    return findings


def _report_due(finding: Finding, state: Mapping[str, Any], trade_count: int, now: datetime, config: Mapping[str, Any]) -> bool:
    reports = state.get("reports") if isinstance(state.get("reports"), Mapping) else {}
    previous = reports.get(finding.key) if isinstance(reports, Mapping) else None
    if not isinstance(previous, Mapping):
        return True
    new_needed = max(1, int(config.get("paper_optimizer_realert_new_trades", 2)))
    if trade_count - int(_f(previous.get("trade_count"))) >= new_needed:
        return True
    last = _parse_time(previous.get("reported_at"))
    cooldown = max(1.0, float(config.get("paper_optimizer_realert_cooldown_hours", 12.0)))
    return last is None or (now - last.astimezone(timezone.utc)).total_seconds() >= cooldown * 3600


def review_paper_parameters(*, paper_state_path: Path, review_state_path: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    paper_state = _load_json(paper_state_path)
    trades = _completed_trades(paper_state)
    findings = _rapid_findings(trades, config) + _comparative_findings(trades, config)
    now = datetime.now(timezone.utc)
    state = _load_json(review_state_path)
    if state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION, "reports": {}}
    reports = state.get("reports") if isinstance(state.get("reports"), Mapping) else {}
    new_findings = [finding for finding in findings if _report_due(finding, state, len(trades), now, config)]
    pending = [finding.key for finding in new_findings]
    payload = {
        "version": STATE_VERSION,
        "completed_trades": len(trades),
        "active_keys": sorted(finding.key for finding in findings),
        "reports": dict(reports),
        "pending_report_keys": pending,
        "findings": [asdict(item) for item in findings],
        "updated_at": now.isoformat(),
    }
    _save_json(review_state_path, payload)
    return {
        "completed_trades": len(trades),
        "findings": [asdict(item) for item in findings],
        "new_findings": [asdict(item) for item in new_findings],
        "alert": bool(new_findings),
        "alert_level": "comparative" if any(item.level == "comparative" for item in new_findings) else "rapid" if new_findings else "none",
        "pending_report_keys": pending,
        "logs": [f"[PARAM] {item.evidence} | {item.recommendation}" for item in new_findings],
    }


def acknowledge_paper_review(review_state_path: Path, keys: Iterable[str]) -> None:
    """Acknowledge only findings whose separate Discord alert was delivered."""
    clean = {str(value) for value in keys if str(value)}
    if not clean:
        return
    state = _load_json(review_state_path)
    if state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION, "reports": {}}
    reports = dict(state.get("reports") or {})
    now = datetime.now(timezone.utc).isoformat()
    count = int(_f(state.get("completed_trades")))
    for key in clean:
        reports[key] = {"trade_count": count, "reported_at": now}
    state["version"] = STATE_VERSION
    state["reports"] = reports
    state["pending_report_keys"] = [key for key in (state.get("pending_report_keys") or []) if key not in clean]
    _save_json(review_state_path, state)
