# r5
"""Fast diagnostic plus evidence-based paper review for CF v6.1.0.

No finding changes trading parameters automatically.  The rapid audit can flag
one objectively poor entry after only one to three closed trades, but labels it
as an early diagnostic rather than statistical proof.  Larger bucket comparisons
remain heuristic comparison evidence unless separately validated out of sample.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable, Mapping

STATE_VERSION = "paper-optimizer-v610-r3"


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
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


@dataclass(frozen=True)
class Finding:
    key: str
    label: str
    samples: int
    average_r: float
    evidence: str
    level: str
    statistically_confirmed: bool = False


def _completed_trades(paper_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def append_close(
        action: Mapping[str, Any],
        details: Mapping[str, Any],
        timestamp: str,
    ) -> None:
        if not bool(details.get("full_close", False)):
            return
        features = details.get("entry_features")
        if not isinstance(features, Mapping):
            return
        if features.get("optimizer_compatible") is False:
            return
        risk = max(1e-9, _f(features.get("risk_usd")))
        pnl = _f(details.get("trade_net_pnl_usd"), _f(action.get("realized_pnl_usd")))
        rows.append(
            {
                "symbol": str(action.get("symbol") or ""),
                "pnl": pnl,
                "r": pnl / risk,
                "win": pnl > 0,
                "features": dict(features),
                "reason": str(action.get("reason") or ""),
                "timestamp": timestamp,
                "holding_minutes": _f(details.get("holding_minutes")),
            }
        )

    for entry in paper_state.get("ledger") or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        details = entry.get("details")
        if not isinstance(action, Mapping) or not isinstance(details, Mapping):
            continue
        timestamp = str(entry.get("timestamp") or "")
        kind = str(action.get("kind"))
        if kind == "CLOSE":
            append_close(action, details, timestamp)
        elif kind == "REVERSE":
            reverse_close = details.get("reverse_close")
            if isinstance(reverse_close, Mapping):
                append_close(action, reverse_close, timestamp)
    return sorted(rows, key=lambda row: str(row.get("timestamp") or ""))


def _stats(rows: Iterable[Mapping[str, Any]]) -> tuple[int, float, float]:
    data = list(rows)
    if not data:
        return 0, 0.0, 0.0
    return (
        len(data),
        sum(bool(row.get("win")) for row in data) / len(data),
        mean(_f(row.get("r")) for row in data),
    )


def _rapid_rules(config: Mapping[str, Any]) -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    stop_minutes = float(config.get("paper_optimizer_rapid_stop_minutes", 4.0))
    return [
        (
            "IMMEDIATE_STOP",
            "Einstieg wurde nahezu sofort ausgestoppt",
            lambda row: (
                _f(row.get("holding_minutes")) <= stop_minutes
                and _f(row.get("r")) < 0
                and (
                    "stop" in str(row.get("reason") or "").lower()
                    or _f(row.get("r")) <= -0.50
                )
            ),
        ),
        (
            "EXTREME_CHASE",
            "Einstieg lief einer bereits extrem überdehnten Bewegung hinterher",
            lambda row: (
                bool(row["features"].get("extremity_available", False))
                and _f(row["features"].get("extremity_score"))
                * int(_f(row["features"].get("direction"))) >= 55.0
            ),
        ),
        (
            "RELATIVE_OPPOSITION",
            "W-Einstieg widersprach der relativen Marktteilnahme",
            lambda row: bool(row["features"].get("reversal_relative_opposition", False)),
        ),
        (
            "LATE_ENTRY",
            "E-Einstieg war bereits alt oder weit verbraucht",
            lambda row: (
                str(row["features"].get("setup") or "") == "E"
                and (
                    _f(row["features"].get("setup_age_minutes"))
                    > float(config.get("paper_early_max_age_minutes", 1))
                    or _f(row["features"].get("setup_consumed_fraction"))
                    > float(config.get("paper_early_max_consumed_fraction", 0.55))
                )
            ),
        ),
        (
            "COST_HEAVY",
            "Ausführungskosten waren im Verhältnis zum Stop zu hoch",
            lambda row: (
                _f(row["features"].get("stop_pct")) > 0
                and _f(row["features"].get("cost_pct"))
                / _f(row["features"].get("stop_pct")) >= 0.35
            ),
        ),
    ]


def _rapid_findings(trades: list[dict[str, Any]], config: Mapping[str, Any]) -> list[Finding]:
    if not bool(config.get("paper_optimizer_rapid_enabled", True)) or not trades:
        return []
    max_rows = max(1, min(3, int(config.get("paper_optimizer_rapid_max_trades", 3))))
    recent = trades[-max_rows:]
    loss_threshold = float(config.get("paper_optimizer_rapid_loss_r", -0.65))
    findings: list[Finding] = []
    for key, label, predicate in _rapid_rules(config):
        affected = [row for row in recent if predicate(row)]
        if not affected:
            continue
        average_r = mean(_f(row.get("r")) for row in affected)
        severe_single = len(affected) == 1 and average_r <= min(loss_threshold, -0.80)
        repeated = len(affected) >= 2 and average_r <= max(loss_threshold, -0.35)
        if not (severe_single or repeated):
            continue
        symbols = ",".join(sorted({str(row.get("symbol") or "?") for row in affected}))
        evidence = (
            f"{label}: n={len(affected)} ({symbols}), ØR {average_r:+.2f}; "
            "früher Diagnosehinweis, keine statistische Bestätigung"
        )
        findings.append(
            Finding(
                key="RAPID_" + key,
                label=label,
                samples=len(affected),
                average_r=round(average_r, 4),
                evidence=evidence,
                level="rapid",
                statistically_confirmed=False,
            )
        )
    return findings


def _feature_rules(config: Mapping[str, Any]) -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    return [
        ("LOW_DATA", "niedrige Datenqualität", lambda f: _f(f.get("data_quality"), 100) < 75),
        ("LOW_READINESS", "Readiness unter 66", lambda f: _f(f.get("readiness"), 100) < 66),
        ("LOW_CONFIDENCE", "Confidence unter 62", lambda f: _f(f.get("confidence"), 100) < 62),
        ("LOW_BTC", "niedriger BTC-Kontext", lambda f: _f(f.get("btc_context"), 58) < 42),
        ("LOW_VOLUME", "schwache Volumenbestätigung", lambda f: _f(f.get("volume_confirmation")) < 50),
        ("LOW_TAPE", "niedrige Tape-Qualität", lambda f: _f(f.get("tape_quality")) < 70),
        ("HIGH_COST", "hohe Roundtrip-Kosten", lambda f: _f(f.get("cost_pct")) > 0.065),
        ("REGIME_AGAINST", "7/14/30D-Regime gegen Einstieg", lambda f: bool(f.get("regime_available", False)) and _f(f.get("regime_modifier")) <= -4),
        ("EXTREME_CHASE", "Einstieg in Richtung einer Überdehnung", lambda f: bool(f.get("extremity_available", False)) and _f(f.get("extremity_score")) * int(_f(f.get("direction"))) >= 45),
        ("FLOW_JUMPY", "stark sprunghafte ER-Struktur", lambda f: bool(f.get("flow_available", False)) and _f(f.get("flow_score")) <= float(config.get("paper_flow_jump_threshold", -45.0))),
        ("FLOW_RUN_AGAINST", "langlebige ER-Struktur gegen Einstieg", lambda f: bool(f.get("flow_available", False)) and _f(f.get("flow_score")) >= float(config.get("paper_flow_run_threshold", 45.0)) and _f(f.get("flow_age_score")) >= float(config.get("paper_flow_long_age_threshold", 50.0)) and int(_f(f.get("flow_direction"))) == -int(_f(f.get("direction")))),
        ("SETUP_T", "T-Setup", lambda f: str(f.get("setup")) == "T"),
        ("SETUP_E", "E-Setup", lambda f: str(f.get("setup")) == "E"),
        ("SETUP_W", "W-Setup", lambda f: str(f.get("setup")) == "W"),
    ]


def _comparative_findings(trades: list[dict[str, Any]], config: Mapping[str, Any]) -> list[Finding]:
    minimum_total = max(8, int(config.get("paper_optimizer_min_total_trades", 8)))
    minimum_bucket = max(3, int(config.get("paper_optimizer_min_bucket_trades", 3)))
    minimum_gap_r = max(0.15, float(config.get("paper_optimizer_min_r_gap", 0.35)))
    maximum_bucket_win_rate = min(0.5, float(config.get("paper_optimizer_max_bucket_win_rate", 0.38)))
    if len(trades) < minimum_total:
        return []
    findings: list[Finding] = []
    for key, label, predicate in _feature_rules(config):
        bucket = [row for row in trades if predicate(row["features"])]
        other = [row for row in trades if not predicate(row["features"])]
        n, win, avg_r = _stats(bucket)
        other_n, other_win, other_avg_r = _stats(other)
        if n < minimum_bucket or other_n < minimum_bucket:
            continue
        symbol_count = len({str(row.get("symbol") or "") for row in bucket if row.get("symbol")})
        if symbol_count < 2 or win > maximum_bucket_win_rate or avg_r >= -0.05:
            continue
        if other_avg_r - avg_r < minimum_gap_r:
            continue
        evidence = (
            f"{label}: n={n}/{symbol_count} Coins, Treffer {win:.0%}, ØR {avg_r:+.2f}; "
            f"Vergleich n={other_n}, Treffer {other_win:.0%}, ØR {other_avg_r:+.2f}; "
            "heuristischer Vergleich, keine Signifikanz-/OOS-Bestätigung"
        )
        findings.append(
            Finding(
                key="COMP_" + key,
                label=label,
                samples=n,
                average_r=round(avg_r, 4),
                evidence=evidence,
                level="comparative",
                statistically_confirmed=False,
            )
        )
    return findings


def review_paper_parameters(
    *,
    paper_state_path: Path,
    review_state_path: Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    paper_state = _load_json(paper_state_path)
    trades = _completed_trades(paper_state)
    findings = _rapid_findings(trades, config) + _comparative_findings(trades, config)

    review_state = _load_json(review_state_path)
    if review_state.get("version") != STATE_VERSION:
        review_state = {"version": STATE_VERSION, "active_keys": [], "reported_keys": []}
    else:
        review_state["version"] = STATE_VERSION
    reported_keys = {str(value) for value in (review_state.get("reported_keys") or []) if value}
    current_active = {finding.key for finding in findings}
    new_findings = [finding for finding in findings if finding.key not in reported_keys]
    pending_report_keys = [finding.key for finding in new_findings]

    _save_json(
        review_state_path,
        {
            "version": STATE_VERSION,
            "completed_trades": len(trades),
            "active_keys": sorted(current_active),
            "reported_keys": sorted(reported_keys),
            "findings": [asdict(item) for item in findings],
        },
    )
    return {
        "completed_trades": len(trades),
        "findings": [asdict(item) for item in findings],
        "new_findings": [asdict(item) for item in new_findings],
        "alert": bool(new_findings),
        "alert_level": (
            "comparative" if any(item.level == "comparative" for item in new_findings)
            else "rapid" if new_findings else "none"
        ),
        "pending_report_keys": pending_report_keys,
        "logs": [
            "[PARAM] Optimierungshinweis (keine automatische Änderung): " + item.evidence
            for item in new_findings
        ],
    }


def acknowledge_paper_review(review_state_path: Path, keys: Iterable[str]) -> None:
    """Acknowledge only findings that were actually delivered to Discord."""
    clean = {str(value) for value in keys if str(value)}
    if not clean:
        return
    state = _load_json(review_state_path)
    if state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION, "active_keys": [], "reported_keys": []}
    reported = {str(value) for value in (state.get("reported_keys") or []) if value}
    reported.update(clean)
    state["version"] = STATE_VERSION
    state["reported_keys"] = sorted(reported)
    _save_json(review_state_path, state)
