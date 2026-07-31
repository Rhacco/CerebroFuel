"""Crypto Signal Monitor v3.8.0 — early swings, T/W and persistent paper trading."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from discord_sender import send_discord
from lighter_monitor import APP_VERSION, LighterMonitor
from paper_trader import PaperTrader
from notification_state import mark_report_sent, report_send_decision

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=f"CF v{APP_VERSION}")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--force-discord", action="store_true")
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

    monitor = LighterMonitor(config)
    report, payload = monitor.run()
    semantic_report = report
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
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
        action_line = paper_result.get("action_line")
        if action_line:
            report += "\n" + str(action_line)
        payload["paper"] = paper_result
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
    if paper_result:
        for line in paper_result["logs"]:
            print(line)

    should_send = (
        not args.no_send
        and os.getenv("SEND_DISCORD", "true").lower() not in {"0", "false", "no", "off"}
    )
    if should_send:
        notification_path = output / "notification_state.json"
        now_ms = int(monitor.generated_at.timestamp() * 1000)
        action_force = bool(paper_result and paper_result.get("actions"))
        send_now, send_reason, digest = report_send_decision(
            path=notification_path,
            report=semantic_report,
            now_ms=now_ms,
            config=config,
            force=bool(args.force_discord or action_force),
        )
        if send_now:
            webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
            if not webhook:
                raise RuntimeError("DISCORD_WEBHOOK_URL fehlt")
            send_discord(
                webhook,
                report,
                username=str(config.get("discord_username", "CF v3.8.0")),
                avatar_url=str(config.get("discord_avatar_url", "")).strip(),
            )
            mark_report_sent(
                path=notification_path,
                digest=digest,
                now_ms=now_ms,
                reason=send_reason,
            )
            print(f"Discord gesendet ({send_reason}).")
        else:
            print("Discord übersprungen (inhaltlich unverändert).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Package revision: v3.8.0-early-swing-r1
