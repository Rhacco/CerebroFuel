"""Crypto Signal Monitor v3.6.3 — Lighter-native, read-only signal engine."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from discord_sender import send_discord
from lighter_monitor import APP_VERSION, LighterMonitor

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=f"CF v{APP_VERSION}")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--force-discord", action="store_true")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if config.get("schema_version") != APP_VERSION:
        raise ValueError(f"config schema_version muss {APP_VERSION} sein")

    report, payload = LighterMonitor(config).run()
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    (output / "latest.txt").write_text(report + "\n", encoding="utf-8")
    (output / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(report)

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
            username=str(config.get("discord_username", "CF v3.6.3")),
            avatar_url=str(config.get("discord_avatar_url", "")).strip(),
        )
        print("Discord gesendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Package revision: v3.6.3-ptw-precision-r4
