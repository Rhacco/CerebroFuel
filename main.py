# r2
"""Crypto Signal Monitor v7.1.0 — live signals, J/E, events and shock protection."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from discord_sender import send_discord
from event_context import load_critical_events
from event_display_state import mark_event_displayed, plan_event_display
from lighter_monitor import APP_VERSION, PACKAGE_REVISION, LighterMonitor

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=f"CF v{APP_VERSION}")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--no-send", action="store_true")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if config.get("schema_version") != APP_VERSION:
        raise ValueError(f"config schema_version muss {APP_VERSION} sein")
    if config.get("package_revision") != PACKAGE_REVISION:
        raise ValueError(f"config package_revision muss {PACKAGE_REVISION} sein")

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    state_ready_path = output / "runtime_state_ready"
    state_ready_path.unlink(missing_ok=True)
    generated_at = datetime.now(timezone.utc)
    event_snapshot = load_critical_events(
        config,
        now=generated_at,
        cache_path=output / "event_cache.json",
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
        daily_candle_cache_path=output / "daily_candle_cache.json",
        springer_history_path=output / "springer_history.json",
        display_selection_state_path=output / "display_selection_state.json",
        now=generated_at,
    )
    payload["events"] = event_snapshot.to_dict()

    (output / "latest.txt").write_text(report + "\n", encoding="utf-8")
    (output / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report)
    for signal in monitor.last_signals:
        if signal.state == "INVALID_DATA":
            reason = "; ".join(signal.reasons) or "unbekannter Datenfehler"
            print(f"[DATA] {signal.symbol}: {reason}")
    state_ready_path.write_text(
        f"{APP_VERSION}-{PACKAGE_REVISION}\n", encoding="utf-8"
    )
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
            username=str(config.get("discord_username", "CF v7.1.0")),
            avatar_url=str(config.get("discord_avatar_url", "")).strip(),
        )
        mark_event_displayed(
            state_path=event_display_path,
            plan=event_plan,
            now=generated_at,
            displayed_symbols=monitor.last_displayed_event_symbols,
        )
        print("Discord als neue Nachricht gesendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
