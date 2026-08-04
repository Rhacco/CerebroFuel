"""Crypto Signal Monitor v5.1.0 — Lighter pool with incident protection."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from discord_sender import send_discord
from lighter_monitor import APP_VERSION, LighterMonitor
from paper_trader import PaperTrader
from event_context import load_critical_events
from event_display_state import mark_event_displayed, plan_event_display
from paper_optimizer import review_paper_parameters

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=f"CF v{APP_VERSION}")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--no-paper", action="store_true")
    parser.add_argument(
        "--paper-state",
        default=os.getenv(
            "PAPER_STATE_PATH",
            str(ROOT / "output" / "paper_state.json"),
        ),
    )
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if config.get("schema_version") != APP_VERSION:
        raise ValueError(f"config schema_version muss {APP_VERSION} sein")

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    generated_at = datetime.now(timezone.utc)
    event_snapshot = load_critical_events(
        config,
        now=generated_at,
        cache_path=output / "event_cache.json",
        local_feed_path=ROOT / "events.json",
    )
    event_display_path = output / "event_display_state.json"
    event_plan = plan_event_display(
        marks=event_snapshot.display_marks,
        now=generated_at,
        timezone_name=str(config.get("timezone", "Europe/Berlin")),
        state_path=event_display_path,
    )
    monitor = LighterMonitor(config)
    report, payload = monitor.run(
        event_marks=event_snapshot.marks,
        event_display_codes=event_plan.codes,
        incident_state_path=output / "incident_state.json",
        signal_transition_state_path=output / "signal_transition_state.json",
        signal_streak_state_path=output / "signal_streak_state.json",
        signal_evaluation_state_path=output / "signal_evaluation_state.json",
        now=generated_at,
    )
    payload["events"] = event_snapshot.to_dict()
    paper_result = None
    if bool(config.get("paper_trading_enabled", True)) and not args.no_paper:
        paper_result = PaperTrader(
            config,
            Path(args.paper_state),
        ).run(
            monitor.last_signals,
            monitor.last_snapshots,
            monitor.generated_at,
        )
        payload["paper"] = paper_result
        parameter_review = review_paper_parameters(
            paper_state_path=Path(args.paper_state),
            review_state_path=output / "parameter_review.json",
            config=config,
        )
        payload["parameter_review"] = parameter_review
        for line in parameter_review.get("logs", []):
            paper_result.setdefault("logs", []).append(str(line))
        if parameter_review.get("alert"):
            alert_line = (
                "Parameter-Optimierung bestätigt!"
                if parameter_review.get("alert_level") == "statistical"
                else "Früher Diagnosehinweis!"
            )
            report += "\n" + alert_line
        payload["report"] = report
        with (output / "paper_decisions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "generated_at": payload["generated_at"],
                        "decision_key": paper_result["decision_key"],
                        "fresh_decision": paper_result["fresh_decision"],
                        "actions": paper_result["actions"],
                        "account": paper_result["account"],
                        "positions": paper_result["positions"],
                        "logs": paper_result["logs"],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    (output / "latest.txt").write_text(report + "\n", encoding="utf-8")
    (output / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(report)
    for signal in monitor.last_signals:
        if signal.state == "INVALID_DATA":
            reason = "; ".join(signal.reasons) or "unbekannter Datenfehler"
            print(f"[DATA] {signal.symbol}: {reason}")
    if paper_result:
        for line in paper_result["logs"]:
            print(line)
    for diagnostic in event_snapshot.diagnostics:
        print(f"EVENT: {diagnostic}")
    if monitor.last_incidents:
        for diagnostic in monitor.last_incidents.diagnostics:
            print(f"INCIDENT: {diagnostic}")

    should_send = (
        not args.no_send
        and os.getenv("SEND_DISCORD", "true").lower() not in {"0", "false", "no", "off"}
    )
    if should_send:
        webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        if not webhook:
            raise RuntimeError("DISCORD_WEBHOOK_URL fehlt")
        send_discord(
            webhook,
            report,
            username=str(config.get("discord_username", "CF v5.1.0")),
            avatar_url=str(config.get("discord_avatar_url", "")).strip(),
        )
        mark_event_displayed(
            state_path=event_display_path,
            plan=event_plan,
            now=generated_at,
        )
        print("Discord als neue Nachricht gesendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

