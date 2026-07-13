"""Entry point for crypto-signal-monitor v3.1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis import (
    CoinAnalysis,
    ShortMetrics,
    analysis_to_dict,
    build_coin_analysis,
    build_report,
    build_short_metrics,
    normalize_history,
)
from discord_sender import send_discord
from lcw_client import LiveCoinWatchClient
from state_client import StateReadError, load_snapshots

ROOT = Path(__file__).resolve().parent


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = ["reference_coin", "groups", "currency", "timezone"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Fehlende config.json-Felder: {', '.join(missing)}")
    return config


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kompakter Krypto-Auffälligkeitsmonitor")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--no-send", action="store_true")
    return parser.parse_args()


def parse_coin(item: Any) -> tuple[str, tuple[str, ...]]:
    if isinstance(item, str):
        code = item.upper()
        return code, (code,)
    if isinstance(item, dict):
        display = str(item.get("display") or item.get("code") or "").upper()
        raw_codes = item.get("codes")
        if isinstance(raw_codes, list):
            codes = tuple(str(code).upper() for code in raw_codes if str(code).strip())
        else:
            code = str(item.get("code") or display).upper()
            codes = (code,) if code else tuple()
        if display and codes:
            return display, tuple(dict.fromkeys(codes))
    raise ValueError(f"Ungültiger Coin-Eintrag: {item!r}")


def parse_layout(
    config: dict[str, Any],
) -> tuple[tuple[str, tuple[str, ...]], list[tuple[str, tuple[str, ...]]]]:
    reference = parse_coin(config["reference_coin"])
    pool: list[tuple[str, tuple[str, ...]]] = []
    seen = {reference[0]}
    for group in config["groups"]:
        items = group.get("coins") if isinstance(group, dict) else group
        if not isinstance(items, list):
            raise ValueError("Jede Gruppe benötigt eine Liste 'coins'.")
        for item in items:
            display, codes = parse_coin(item)
            if display in seen:
                continue
            seen.add(display)
            pool.append((display, codes))
    return reference, pool


def refresh_histories(
    *,
    client: LiveCoinWatchClient,
    codes: list[str],
    start_ms: int,
    end_ms: int,
    workers: int,
) -> tuple[dict[str, list], list[str]]:
    histories: dict[str, list] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(client.get_history, code, start_ms, end_ms): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                histories[code] = normalize_history(future.result())
                print(f"Frische Historie: {code} ({len(histories[code])} Punkte)")
            except Exception as exc:  # individual coin failure must not kill report
                failed.append(code)
                print(f"WARNUNG: Historie {code} fehlgeschlagen: {exc}", file=sys.stderr)
    return histories, failed


def resolve_pair(
    pair: tuple[str, tuple[str, ...]],
    current_by_code: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    display, candidates = pair
    for candidate in candidates:
        if candidate in current_by_code:
            return display, candidate
    return None


def run() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    api_key = os.getenv("LCW_API_KEY", "").strip()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    state_url = os.getenv("CF_STATE_URL", "").strip()
    state_key = os.getenv("CF_STATE_KEY", "").strip()
    should_send = env_bool("SEND_DISCORD", True) and not args.no_send
    if not api_key:
        raise ValueError("GitHub Secret LCW_API_KEY fehlt.")
    if should_send and not webhook_url:
        raise ValueError("GitHub Secret DISCORD_WEBHOOK_URL fehlt.")

    reference_pair, pool_pairs = parse_layout(config)
    all_pairs = [reference_pair, *pool_pairs]
    candidate_codes = list(dict.fromkeys(code for _, codes in all_pairs for code in codes))
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    client = LiveCoinWatchClient(
        api_key=api_key,
        currency=str(config.get("currency", "USD")),
        timeout=int(config.get("request_timeout_seconds", 30)),
    )
    print(f"Lade aktuelle Daten für {len(candidate_codes)} LCW-Codes ...")
    current_by_code = client.get_coins(candidate_codes)

    try:
        snapshots = load_snapshots(
            state_url,
            state_key,
            timeout=int(config.get("request_timeout_seconds", 30)),
        )
        print(f"Cloudflare-Zeitdaten: {len(snapshots)} Snapshots.")
    except StateReadError as exc:
        snapshots = []
        print(f"WARNUNG: {exc}; V10/20/60 erscheinen ggf. ⚪.", file=sys.stderr)

    resolved_reference = resolve_pair(reference_pair, current_by_code)
    if resolved_reference is None:
        raise ValueError(f"Referenzcoin {reference_pair[0]} fehlt in LCW.")
    reference_display, reference_api = resolved_reference
    reference_current = current_by_code[reference_api]

    btc_short = build_short_metrics(
        api_code=reference_api,
        current=reference_current,
        snapshots=snapshots,
        now_ms=now_ms,
        btc_price_changes=None,
        config=config,
        is_reference=True,
    )

    resolved_pool: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for pair in pool_pairs:
        resolved = resolve_pair(pair, current_by_code)
        if resolved is None:
            unresolved.append(pair[0])
        else:
            resolved_pool.append(resolved)
    if unresolved:
        print("WARNUNG: Keine LCW-Daten für: " + ", ".join(unresolved), file=sys.stderr)

    short_by_code: dict[str, ShortMetrics] = {}
    for display, api_code in resolved_pool:
        short_by_code[api_code] = build_short_metrics(
            api_code=api_code,
            current=current_by_code[api_code],
            snapshots=snapshots,
            now_ms=now_ms,
            btc_price_changes=btc_short.price_changes,
            config=config,
            is_reference=False,
        )

    top_count = int(config.get("top_coin_count", 8))
    ranked = sorted(
        resolved_pool,
        key=lambda pair: (
            short_by_code[pair[1]].anomaly_score,
            abs(short_by_code[pair[1]].pressure_score or 0.0),
        ),
        reverse=True,
    )
    selected = ranked[:top_count]
    if len(selected) < top_count:
        print(
            f"WARNUNG: Nur {len(selected)} statt {top_count} Coins auflösbar.",
            file=sys.stderr,
        )

    history_days = int(config.get("history_days", 42))
    start_ms = int((now - timedelta(days=history_days)).timestamp() * 1000)
    history_codes = list(dict.fromkeys([reference_api, *(api for _, api in selected)]))
    print(f"Lade {len(history_codes)} Historien frisch ({history_days} Tage) ...")
    histories, failed_history = refresh_histories(
        client=client,
        codes=history_codes,
        start_ms=start_ms,
        end_ms=now_ms,
        workers=int(config.get("history_parallel_requests", 8)),
    )

    common = {
        "now": now,
        "timezone": str(config.get("timezone", "Europe/Berlin")),
        "block_hours": int(config.get("time_block_hours", 4)),
        "min_samples": int(config.get("seasonality_min_samples", 4)),
        "config": config,
    }
    reference_analysis = build_coin_analysis(
        display_code=reference_display,
        api_code=reference_api,
        current=reference_current,
        short=btc_short,
        history=histories.get(reference_api, []),
        is_reference=True,
        **common,
    )
    top_analyses: list[CoinAnalysis] = []
    for display, api_code in selected:
        top_analyses.append(
            build_coin_analysis(
                display_code=display,
                api_code=api_code,
                current=current_by_code[api_code],
                short=short_by_code[api_code],
                history=histories.get(api_code, []),
                is_reference=False,
                **common,
            )
        )

    report = build_report(
        reference_analysis,
        top_analyses,
        generated_at=now,
        timezone=str(config.get("timezone", "Europe/Berlin")),
    )
    output_dir = ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    (output_dir / "latest_report.txt").write_text(report + "\n", encoding="utf-8")
    (output_dir / "latest_analysis.json").write_text(
        json.dumps(
            {
                "version": "3.1",
                "generated_at": now.isoformat(),
                "snapshot_count": len(snapshots),
                "reference": analysis_to_dict(reference_analysis),
                "top_coins": [analysis_to_dict(item) for item in top_analyses],
                "unresolved": unresolved,
                "history_failures": failed_history,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print("\n" + report + "\n", flush=True)
    if failed_history:
        print("Historie fehlgeschlagen für: " + ", ".join(sorted(failed_history)), file=sys.stderr)
    if should_send:
        send_discord(
            webhook_url=webhook_url,
            content=report,
            username=str(config.get("discord_username", "Krypto-Monitor")),
            timeout=int(config.get("request_timeout_seconds", 30)),
        )
        print("Discord-Nachricht gesendet.")
    else:
        print("Testmodus: keine Discord-Nachricht gesendet.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
