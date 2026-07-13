"""Entry point for the crypto signal monitor."""

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
    analysis_to_dict,
    analyze_coin,
    build_report,
    delta_to_pct,
    normalize_history,
)
from discord_sender import send_discord
from history_store import load_cache, save_cache
from lcw_client import LiveCoinWatchClient

ROOT = Path(__file__).resolve().parent


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = ["reference_coin", "groups", "currency", "timezone"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Fehlende config.json-Felder: {', '.join(missing)}")
    if not isinstance(config["groups"], list) or not config["groups"]:
        raise ValueError("config.json: 'groups' muss mindestens eine Gruppe enthalten.")
    return config


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kompakter Krypto-BUY/SELL-Monitor")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--force-history-refresh", action="store_true")
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
    raise ValueError(f"Ungültiger Coin-Eintrag in config.json: {item!r}")


def parse_layout(
    config: dict[str, Any],
) -> tuple[tuple[str, tuple[str, ...]], list[list[tuple[str, tuple[str, ...]]]]]:
    reference = parse_coin(config["reference_coin"])
    groups: list[list[tuple[str, tuple[str, ...]]]] = []
    seen_displays = {reference[0]}
    for group in config["groups"]:
        items = group.get("coins") if isinstance(group, dict) else group
        if not isinstance(items, list):
            raise ValueError("Jede Gruppe benötigt eine Liste 'coins'.")
        parsed: list[tuple[str, tuple[str, ...]]] = []
        for item in items:
            display, api_codes = parse_coin(item)
            if display in seen_displays:
                continue
            seen_displays.add(display)
            parsed.append((display, api_codes))
        groups.append(parsed)
    return reference, groups


def refresh_histories(
    *,
    client: LiveCoinWatchClient,
    codes: list[str],
    existing: dict[str, list],
    start_ms: int,
    end_ms: int,
    workers: int,
) -> tuple[dict[str, list], list[str]]:
    histories = dict(existing)
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
                print(f"Historie aktualisiert: {code} ({len(histories[code])} Punkte)")
            except Exception as exc:
                failed.append(code)
                print(f"WARNUNG: Historie {code} fehlgeschlagen: {exc}", file=sys.stderr)
    return histories, failed


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

    reference_pair, group_pairs = parse_layout(config)
    all_pairs = [reference_pair, *(pair for group in group_pairs for pair in group)]
    candidate_codes = list(
        dict.fromkeys(code for _, codes in all_pairs for code in codes)
    )

    now = datetime.now(timezone.utc)
    history_days = int(config.get("history_days", 90))
    refresh_hours = float(config.get("history_refresh_hours", 6))
    start_ms = int((now - timedelta(days=history_days)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    client = LiveCoinWatchClient(
        api_key=api_key,
        currency=str(config.get("currency", "USD")),
        timeout=int(config.get("request_timeout_seconds", 30)),
    )

    print(
        f"Lade aktuelle Daten für {len(candidate_codes)} LCW-Codes in einem /coins/map-Aufruf ..."
    )
    current_by_code = client.get_coins(candidate_codes)

    def resolve(pair: tuple[str, tuple[str, ...]]) -> tuple[str, str] | None:
        display, candidates = pair
        for candidate in candidates:
            if candidate in current_by_code:
                return display, candidate
        return None

    resolved_reference = resolve(reference_pair)
    if resolved_reference is None:
        raise ValueError(
            f"Referenzcoin {reference_pair[0]} fehlt in LCW "
            f"({', '.join(reference_pair[1])})."
        )
    reference_display, reference_api = resolved_reference

    resolved_groups: list[list[tuple[str, str]]] = []
    unresolved: list[str] = []
    for group in group_pairs:
        resolved_group: list[tuple[str, str]] = []
        for pair in group:
            resolved = resolve(pair)
            if resolved is None:
                unresolved.append(pair[0])
            else:
                resolved_group.append(resolved)
        resolved_groups.append(resolved_group)
    selected_api_codes = list(
        dict.fromkeys([reference_api, *(code for group in resolved_groups for _, code in group)])
    )
    if unresolved:
        print(
            "WARNUNG: Keine aktuellen LCW-Daten für: " + ", ".join(unresolved),
            file=sys.stderr,
        )

    cache_path = ROOT / "state" / "history_cache.json"
    fetched_at, histories = load_cache(cache_path)
    cache_age = None if fetched_at is None else now - fetched_at.astimezone(timezone.utc)
    refresh_due = (
        args.force_history_refresh
        or fetched_at is None
        or cache_age is None
        or cache_age >= timedelta(hours=refresh_hours)
    )

    output_dir = ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    refresh_flag = output_dir / "history_refreshed.flag"
    if refresh_flag.exists():
        refresh_flag.unlink()

    if refresh_due:
        print(f"Aktualisiere LCW-Historien ({history_days} Tage; Intervall {refresh_hours:g}h) ...")
        histories, failed_history = refresh_histories(
            client=client,
            codes=selected_api_codes,
            existing=histories,
            start_ms=start_ms,
            end_ms=end_ms,
            workers=int(config.get("history_parallel_requests", 6)),
        )
        save_cache(
            cache_path,
            fetched_at=now,
            histories=histories,
            history_days=history_days,
        )
        refresh_flag.write_text("refreshed\n", encoding="utf-8")
        fetched_at = now
        if failed_history:
            print("WARNUNG: Historie nicht aktualisiert für: " + ", ".join(sorted(failed_history)), file=sys.stderr)
    else:
        age_hours = cache_age.total_seconds() / 3600 if cache_age else 0
        print(f"Nutze History-Cache ({age_hours:.1f}h alt).")

    reference_current = current_by_code[reference_api]
    reference_delta = reference_current.get("delta") or {}
    btc_day = delta_to_pct(reference_delta.get("day"))
    btc_week = delta_to_pct(reference_delta.get("week"))
    common = {
        "btc_day_pct": btc_day,
        "btc_week_pct": btc_week,
        "now": now,
        "timezone": str(config.get("timezone", "Europe/Berlin")),
        "block_hours": int(config.get("time_block_hours", 4)),
        "min_samples": int(config.get("seasonality_min_samples", 4)),
        "recommendation_threshold": int(config.get("recommendation_threshold", 6)),
    }

    reference_analysis = analyze_coin(
        display_code=reference_display,
        api_code=reference_api,
        current=reference_current,
        history=histories.get(reference_api, []),
        is_reference=True,
        **common,
    )

    grouped_analyses: list[list[CoinAnalysis]] = []
    skipped: list[str] = list(unresolved)
    for group in resolved_groups:
        analyses: list[CoinAnalysis] = []
        for display, api_code in group:
            current = current_by_code.get(api_code)
            if current is None:
                skipped.append(display)
                continue
            analyses.append(
                analyze_coin(
                    display_code=display,
                    api_code=api_code,
                    current=current,
                    history=histories.get(api_code, []),
                    is_reference=False,
                    **common,
                )
            )
        grouped_analyses.append(analyses)

    report = build_report(reference_analysis, grouped_analyses)
    (output_dir / "latest_report.txt").write_text(report + "\n", encoding="utf-8")
    (output_dir / "latest_analysis.json").write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "history_fetched_at": fetched_at.isoformat() if fetched_at else None,
                "reference": analysis_to_dict(reference_analysis),
                "groups": [
                    [analysis_to_dict(item) for item in group] for group in grouped_analyses
                ],
                "skipped": skipped,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print("\n" + report + "\n", flush=True)
    if skipped:
        print("Übersprungen: " + ", ".join(skipped), file=sys.stderr)

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
