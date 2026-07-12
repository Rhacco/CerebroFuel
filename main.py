"""Entry point for the crypto signal monitor."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis import (
    analysis_to_dict,
    analyze_coin,
    build_report,
    delta_to_pct,
    normalize_history,
)
from discord_sender import send_discord
from lcw_client import LiveCoinWatchClient

ROOT = Path(__file__).resolve().parent


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    required = ["reference_coin", "coins", "currency", "timezone"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Fehlende config.json-Felder: {', '.join(missing)}")
    if not isinstance(config["coins"], list) or not config["coins"]:
        raise ValueError("config.json: 'coins' muss mindestens einen Coin enthalten.")
    return config


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HBAR/UNI-Kryptomonitor")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="Bericht berechnen und anzeigen, aber nicht an Discord senden.",
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    api_key = os.getenv("LCW_API_KEY", "").strip()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    should_send = env_bool("SEND_DISCORD", True) and not args.no_send

    if not api_key:
        raise ValueError("GitHub Secret LCW_API_KEY fehlt.")
    if should_send and not webhook_url:
        raise ValueError("GitHub Secret DISCORD_WEBHOOK_URL fehlt.")

    now = datetime.now(timezone.utc)
    history_days = int(config.get("history_days", 42))
    start = now - timedelta(days=history_days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    client = LiveCoinWatchClient(
        api_key=api_key,
        currency=str(config.get("currency", "USD")),
        timeout=int(config.get("request_timeout_seconds", 30)),
    )

    reference_code = str(config["reference_coin"]).upper()
    coin_codes = [str(code).upper() for code in config["coins"]]
    all_codes = list(dict.fromkeys([reference_code, *coin_codes]))

    current_by_code: dict[str, dict[str, Any]] = {}
    history_by_code = {}
    for code in all_codes:
        print(f"Lade {code} ...", flush=True)
        current_by_code[code] = client.get_coin(code)
        history_by_code[code] = normalize_history(
            client.get_history(code, start_ms, end_ms)
        )

    reference_delta = current_by_code[reference_code].get("delta") or {}
    btc_day = delta_to_pct(reference_delta.get("day"))
    btc_week = delta_to_pct(reference_delta.get("week"))
    thresholds = config.get("signal_thresholds") or {}

    analyses = [
        analyze_coin(
            code=code,
            current=current_by_code[code],
            history=history_by_code[code],
            btc_day_pct=btc_day,
            btc_week_pct=btc_week,
            now=now,
            timezone=str(config.get("timezone", "Europe/Berlin")),
            block_hours=int(config.get("time_block_hours", 4)),
            min_samples=int(config.get("seasonality_min_samples", 3)),
            entry_threshold=float(thresholds.get("entry", 3.0)),
            exit_threshold=float(thresholds.get("exit", -2.5)),
        )
        for code in coin_codes
    ]

    report = build_report(
        now=now,
        timezone=str(config.get("timezone", "Europe/Berlin")),
        reference_code=reference_code,
        reference_current=current_by_code[reference_code],
        analyses=analyses,
        history_days=history_days,
    )

    output_dir = ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    (output_dir / "latest_report.txt").write_text(report + "\n", encoding="utf-8")
    (output_dir / "latest_analysis.json").write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "reference": {
                    "code": reference_code,
                    "price": current_by_code[reference_code].get("rate"),
                    "day_pct": btc_day,
                    "week_pct": btc_week,
                },
                "coins": [analysis_to_dict(item) for item in analyses],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print("\n" + report + "\n", flush=True)

    if should_send:
        send_discord(
            webhook_url=webhook_url,
            content=report,
            username=str(config.get("discord_username", "Krypto-Monitor")),
            timeout=int(config.get("request_timeout_seconds", 30)),
        )
        print("Discord-Nachricht gesendet.", flush=True)
    else:
        print("Testmodus: keine Discord-Nachricht gesendet.", flush=True)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as exc:  # GitHub Actions should clearly mark the run as failed.
        print(f"FEHLER: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
