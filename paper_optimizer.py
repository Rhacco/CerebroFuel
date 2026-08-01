"""Evidence-based review of paper-trading entry parameters for CF v3.8.4.

The reviewer never changes live parameters. It only flags a potential problem
when enough completed paper trades show a large, repeatable underperformance of
one recorded entry condition versus the remaining sample.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable, Mapping

STATE_VERSION = "paper-optimizer-v383-r1"


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


@dataclass(frozen=True)
class Finding:
    key: str
    label: str
    samples: int
    comparison_samples: int
    win_rate: float
    comparison_win_rate: float
    average_r: float
    comparison_average_r: float
    symbols: int
    first_half_average_r: float
    second_half_average_r: float
    evidence: str


def _completed_trades(paper_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in paper_state.get("ledger") or []:
        if not isinstance(entry, Mapping):
            continue
        action = entry.get("action")
        details = entry.get("details")
        if not isinstance(action, Mapping) or not isinstance(details, Mapping):
            continue
        if str(action.get("kind")) != "CLOSE" or not bool(details.get("full_close", False)):
            continue
        features = details.get("entry_features")
        if not isinstance(features, Mapping):
            continue
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
                "timestamp": str(entry.get("timestamp") or ""),
            }
        )
    return rows


def _stats(rows: Iterable[Mapping[str, Any]]) -> tuple[int, float, float]:
    data = list(rows)
    if not data:
        return 0, 0.0, 0.0
    return len(data), sum(bool(row.get("win")) for row in data) / len(data), mean(_f(row.get("r")) for row in data)


def _feature_rules() -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool]]]:
    return [
        ("LOW_DATA", "niedrige Datenqualität", lambda f: _f(f.get("data_quality"), 100) < 75),
        ("LOW_READINESS", "Readiness unter 68", lambda f: _f(f.get("readiness"), 100) < 68),
        ("LOW_CONFIDENCE", "Confidence unter 64", lambda f: _f(f.get("confidence"), 100) < 64),
        ("LOW_BTC", "niedriger BTC-Kontext", lambda f: _f(f.get("btc_context"), 58) < 45),
        ("LOW_VOLUME", "schwache Volumenbestätigung", lambda f: _f(f.get("volume_confirmation")) < 52),
        ("LOW_TAPE", "niedrige Tape-Qualität", lambda f: _f(f.get("tape_quality")) < 74),
        ("LOW_EXEC", "niedriger Execution-Score", lambda f: _f(f.get("execution_score")) < 62),
        ("HIGH_COST", "hohe Roundtrip-Kosten", lambda f: _f(f.get("cost_pct")) > 0.065),
        ("FUNDING_MISSING", "fehlendes Funding", lambda f: bool(f.get("funding_missing", False))),
        ("EVENT_RISK", "erhöhtes Ereignisrisiko", lambda f: _f(f.get("event_risk")) >= 45),
        ("REGIME_AGAINST", "7/14/30D-Regime gegen Einstieg", lambda f: bool(f.get("regime_available", False)) and _f(f.get("regime_modifier")) <= -4),
        ("REGIME_WEAK", "schwach bestätigtes Mehrwochen-Regime", lambda f: bool(f.get("regime_available", False)) and _f(f.get("regime_consistency")) < 0.67),
        ("LOW_REBOUND_PARTICIPATION", "schwache Teilnahme an BTC-Erholung", lambda f: f.get("rebound_participation") is not None and _f(f.get("rebound_participation"), 1.0) < 0.35),
        ("HIGH_LEVERAGE", "Hebel ab 25x", lambda f: _f(f.get("leverage")) >= 25),
        ("PROBE", "Probe-Einstieg", lambda f: bool(f.get("probe_entry", False))),
        ("PHASE_STRONG", "noch nicht vollständig bereite Strong-Phase", lambda f: str(f.get("setup_phase")) == "strong"),
        ("HIGH_CONSUMED", "bereits weit verbrauchter Bewegungsweg", lambda f: _f(f.get("setup_consumed_fraction")) > 0.55),
        ("SETUP_T", "T-Setup", lambda f: str(f.get("setup")) == "T"),
        ("SETUP_E", "E-Setup", lambda f: str(f.get("setup")) == "E"),
        ("SETUP_W", "W-Setup", lambda f: str(f.get("setup")) == "W"),
    ]


def review_paper_parameters(
    *,
    paper_state_path: Path,
    review_state_path: Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    paper_state = _load_json(paper_state_path)
    trades = _completed_trades(paper_state)
    minimum_total = max(12, int(config.get("paper_optimizer_min_total_trades", 16)))
    minimum_bucket = max(5, int(config.get("paper_optimizer_min_bucket_trades", 6)))
    minimum_gap_r = max(0.15, float(config.get("paper_optimizer_min_r_gap", 0.35)))
    maximum_bucket_win_rate = min(0.5, float(config.get("paper_optimizer_max_bucket_win_rate", 0.38)))

    findings: list[Finding] = []
    if len(trades) >= minimum_total:
        for key, label, predicate in _feature_rules():
            bucket = [row for row in trades if predicate(row["features"])]
            other = [row for row in trades if not predicate(row["features"])]
            n, win, avg_r = _stats(bucket)
            other_n, other_win, other_avg_r = _stats(other)
            if n < minimum_bucket or other_n < minimum_bucket:
                continue
            symbol_count = len({str(row.get("symbol") or "") for row in bucket if row.get("symbol")})
            if symbol_count < 2:
                continue
            ordered_bucket = sorted(bucket, key=lambda row: str(row.get("timestamp") or ""))
            midpoint = len(ordered_bucket) // 2
            first_half = ordered_bucket[:midpoint]
            second_half = ordered_bucket[midpoint:]
            if min(len(first_half), len(second_half)) < 3:
                continue
            _, _, first_avg_r = _stats(first_half)
            _, _, second_avg_r = _stats(second_half)
            if first_avg_r >= 0 or second_avg_r >= 0:
                continue
            if win > maximum_bucket_win_rate:
                continue
            if avg_r >= -0.05:
                continue
            if other_avg_r - avg_r < minimum_gap_r:
                continue
            evidence = (
                f"{label}: n={n}/{symbol_count} Coins, Treffer {win:.0%}, ØR {avg_r:+.2f}, "
                f"Hälften {first_avg_r:+.2f}/{second_avg_r:+.2f}; "
                f"Vergleich n={other_n}, Treffer {other_win:.0%}, ØR {other_avg_r:+.2f}"
            )
            findings.append(
                Finding(
                    key=key,
                    label=label,
                    samples=n,
                    comparison_samples=other_n,
                    win_rate=round(win, 4),
                    comparison_win_rate=round(other_win, 4),
                    average_r=round(avg_r, 4),
                    comparison_average_r=round(other_avg_r, 4),
                    symbols=symbol_count,
                    first_half_average_r=round(first_avg_r, 4),
                    second_half_average_r=round(second_avg_r, 4),
                    evidence=evidence,
                )
            )

    review_state = _load_json(review_state_path)
    if review_state.get("version") != STATE_VERSION:
        review_state = {"version": STATE_VERSION, "active_keys": []}
    previous_active = {
        str(value) for value in (review_state.get("active_keys") or []) if value
    }
    current_active = {finding.key for finding in findings}
    # Discord is alerted once when a statistically supported problem first
    # becomes active. Updated sample counts remain available in JSON, but do
    # not generate a fresh alert every time another trade closes. If a finding
    # later resolves and then reappears, it can alert again.
    new_findings = [finding for finding in findings if finding.key not in previous_active]

    _save_json(
        review_state_path,
        {
            "version": STATE_VERSION,
            "completed_trades": len(trades),
            "active_keys": sorted(current_active),
            "findings": [asdict(item) for item in findings],
        },
    )
    return {
        "completed_trades": len(trades),
        "findings": [asdict(item) for item in findings],
        "new_findings": [asdict(item) for item in new_findings],
        "alert": bool(new_findings),
        "logs": [
            f"[PARAM] statistischer Optimierungshinweis (keine automatische Änderung): {item.evidence}"
            for item in new_findings
        ],
    }
