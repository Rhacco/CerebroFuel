# r1
"""Crypto Signal Monitor v7.0.0 — unified J/E, events and shock protection."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from discord_sender import send_discord
from lighter_monitor import APP_VERSION, PACKAGE_REVISION, LighterMonitor
from paper_trader import PaperTrader
from event_context import load_critical_events
from event_display_state import mark_event_displayed, plan_event_display
from paper_optimizer import acknowledge_paper_review, review_paper_parameters

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
    if config.get("package_revision") != PACKAGE_REVISION:
        raise ValueError(f"config package_revision muss {PACKAGE_REVISION} sein")

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
        event_source_health=event_snapshot.source_health,
        incident_state_path=output / "incident_state.json",
        signal_transition_state_path=output / "signal_transition_state.json",
        signal_streak_state_path=output / "signal_streak_state.json",
        signal_evaluation_state_path=output / "signal_evaluation_state.json",
        daily_candle_cache_path=output / "daily_candle_cache.json",
        now=generated_at,
    )
    payload["events"] = event_snapshot.to_dict()
    paper_result = None
    parameter_review = None
    parameter_alert_text: str | None = None
    review_state_path = output / "parameter_review.json"
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
            review_state_path=review_state_path,
            config=config,
        )
        payload["parameter_review"] = parameter_review
        for line in parameter_review.get("logs", []):
            paper_result.setdefault("logs", []).append(str(line))
        if parameter_review.get("alert"):
            parameter_alert_text = (
                "Parameter-Vergleichshinweis!"
                if parameter_review.get("alert_level") == "comparative"
                else "Früher Diagnosehinweis!"
            )
        payload["report"] = report
    (output / "latest.txt").write_text(report + "\n", encoding="utf-8")
    (output / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(report)
    if parameter_alert_text:
        print(parameter_alert_text)
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
            username=str(config.get("discord_username", "CF v7.0.0")),
            avatar_url=str(config.get("discord_avatar_url", "")).strip(),
        )
        mark_event_displayed(
            state_path=event_display_path,
            plan=event_plan,
            now=generated_at,
            displayed_symbols=monitor.last_header_event_symbols,
        )
        # Parameter hints are deliberately a separate one-line Discord message:
        # the fixed monitor report therefore always keeps its configured line
        # count. A finding is acknowledged only after that alert was actually
        # delivered; a failed alert remains pending for the next run.
        if parameter_alert_text:
            send_discord(
                webhook,
                parameter_alert_text,
                username=str(config.get("discord_username", "CF v7.0.0")),
                avatar_url=str(config.get("discord_avatar_url", "")).strip(),
            )
        if parameter_review is not None and (not parameter_review.get("alert") or parameter_alert_text):
            acknowledge_paper_review(
                review_state_path,
                parameter_review.get("pending_report_keys", []),
            )
        print("Discord als neue Nachricht gesendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

