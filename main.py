"""Entry point for crypto-signal-monitor v3.2.3."""

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
    pre_anomaly_score,
)
from discord_sender import send_discord
from lcw_client import LiveCoinWatchClient

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


def resolve_pair(
    pair: tuple[str, tuple[str, ...]],
    current_by_code: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    display, candidates = pair
    for candidate in candidates:
        if candidate in current_by_code:
            return display, candidate
    return None


def refresh_histories(
    *,
    client: LiveCoinWatchClient,
    codes: list[str],
    start_ms: int,
    end_ms: int,
    workers: int,
    label: str,
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
                print(f"{label}: {code} ({len(histories[code])} Punkte)")
            except Exception as exc:
                failed.append(code)
                print(f"WARNUNG: {label} {code} fehlgeschlagen: {exc}", file=sys.stderr)
    return histories, failed



def log_quality(display: str, short: ShortMetrics) -> None:
    if not short.quality_reasons:
        return
    details = ", ".join(
        f"{window}m={reason}" for window, reason in sorted(short.quality_reasons.items())
    )
    print(f"Datenhinweis {display}: {details}", file=sys.stderr)

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
    print(f"Lade frische Map-Daten für {len(candidate_codes)} LCW-Codes ...")
    current_by_code = client.get_coins(candidate_codes)

    resolved_reference = resolve_pair(reference_pair, current_by_code)
    if resolved_reference is None:
        raise ValueError(f"Referenzcoin {reference_pair[0]} fehlt in LCW.")
    reference_display, reference_api = resolved_reference
    reference_current = current_by_code[reference_api]

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

    preselect_count = max(
        int(config.get("top_coin_count", 8)),
        int(config.get("preselect_coin_count", 12)),
    )
    preselected = sorted(
        resolved_pool,
        key=lambda pair: pre_anomaly_score(current_by_code[pair[1]], reference_current),
        reverse=True,
    )[:preselect_count]

    short_minutes = int(config.get("short_history_minutes", 90))
    short_start_ms = int((now - timedelta(minutes=short_minutes)).timestamp() * 1000)
    short_codes = list(dict.fromkeys([reference_api, *(api for _, api in preselected)]))
    print(f"Lade frische Kurzzeithistorien für {len(short_codes)} Coins ({short_minutes} Min) ...")
    short_histories, short_failures = refresh_histories(
        client=client,
        codes=short_codes,
        start_ms=short_start_ms,
        end_ms=now_ms,
        workers=int(config.get("history_parallel_requests", 8)),
        label="Kurzzeit",
    )

    btc_short = build_short_metrics(
        current=reference_current,
        short_history=short_histories.get(reference_api, []),
        now_ms=now_ms,
        btc_price_changes=None,
        config=config,
        is_reference=True,
    )
    log_quality(reference_display, btc_short)

    short_by_code: dict[str, ShortMetrics] = {}
    for display, api_code in preselected:
        short_by_code[api_code] = build_short_metrics(
            current=current_by_code[api_code],
            short_history=short_histories.get(api_code, []),
            now_ms=now_ms,
            btc_price_changes=btc_short.price_changes,
            config=config,
            is_reference=False,
        )
        log_quality(display, short_by_code[api_code])

    top_count = int(config.get("top_coin_count", 8))
    selected = sorted(
        preselected,
        key=lambda pair: (
            short_by_code[pair[1]].anomaly_score,
            pre_anomaly_score(current_by_code[pair[1]], reference_current),
        ),
        reverse=True,
    )[:top_count]

    history_days = int(config.get("history_days", 42))
    long_start_ms = int((now - timedelta(days=history_days)).timestamp() * 1000)
    long_codes = list(dict.fromkeys([reference_api, *(api for _, api in selected)]))
    print(f"Lade frische Langzeithistorien für {len(long_codes)} Coins ({history_days} Tage) ...")
    long_histories, long_failures = refresh_histories(
        client=client,
        codes=long_codes,
        start_ms=long_start_ms,
        end_ms=now_ms,
        workers=int(config.get("history_parallel_requests", 8)),
        label="Langzeit",
    )

    common = {
        "now": now,
        "timezone": str(config.get("timezone", "Europe/Berlin")),
        "block_hours": int(config.get("time_block_hours", 4)),
        "min_samples": int(config.get("seasonality_min_samples", 4)),
        "minimum_observations": int(config.get("seasonality_min_observations", 20)),
        "config": config,
    }
    reference_analysis = build_coin_analysis(
        display_code=reference_display,
        api_code=reference_api,
        current=reference_current,
        short=btc_short,
        history=long_histories.get(reference_api, []),
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
                history=long_histories.get(api_code, []),
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
                "version": "3.2.3",
                "generated_at": now.isoformat(),
                "reference": analysis_to_dict(reference_analysis),
                "top_coins": [analysis_to_dict(item) for item in top_analyses],
                "unresolved": unresolved,
                "short_history_failures": short_failures,
                "long_history_failures": long_failures,
                "api_requests_expected": 1 + len(short_codes) + len(long_codes),
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
