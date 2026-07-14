"""Entry point for crypto-signal-monitor v3.2.6 reliable-cache refresh."""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from analysis import (
    CoinAnalysis,
    ShortMetrics,
    analysis_to_dict,
    build_coin_analysis,
    build_report,
    build_short_metrics,
    confidence_sort_key,
    normalize_history,
    pre_anomaly_score,
)
from daily_context import (
    STATE_REVISION,
    STATE_VERSION,
    InsufficientDailyHistory,
    build_daily_coin_context,
    carry_forward_context,
    context_for_coin,
    context_is_complete,
    load_state,
    local_day_key,
    save_state,
    state_fingerprint,
)
from discord_sender import send_discord
from lcw_client import LiveCoinWatchClient

ROOT = Path(__file__).resolve().parent
DAILY_STATE_PATH = ROOT / ".cache" / "seasonality" / "state.json"
DAILY_SAVE_MARKER = ROOT / ".cache" / "seasonality" / "save_required"


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


def _turnover_pct(row: dict[str, Any]) -> float:
    volume = max(float(row.get("volume") or 0.0), 0.0)
    cap = max(float(row.get("cap") or 0.0), 0.0)
    return volume / cap * 100.0 if cap > 0 else 0.0


def balanced_preselection(
    pool: list[tuple[str, str]],
    current_by_code: dict[str, dict[str, Any]],
    reference_current: dict[str, Any],
    count: int,
    slot: int,
) -> list[tuple[str, str]]:
    """Every configured coin is map-scored; strongest flash candidates are guaranteed in."""
    ranked = sorted(
        pool,
        key=lambda pair: pre_anomaly_score(current_by_code[pair[1]], reference_current),
        reverse=True,
    )
    selected: list[tuple[str, str]] = []

    def add(pair: tuple[str, str]) -> None:
        if pair not in selected and len(selected) < count:
            selected.append(pair)

    # Most slots react immediately to current map anomalies across the complete pool.
    for pair in ranked[: max(10, count - 5)]:
        add(pair)
    # High-turnover coins are useful for quiet-price / rising-activity setups.
    for pair in sorted(pool, key=lambda pair: _turnover_pct(current_by_code[pair[1]]), reverse=True):
        if len(selected) >= count - 2:
            break
        add(pair)
    # Two rotating slots prevent permanently quiet coins from being ignored.
    remaining = sorted((pair for pair in pool if pair not in selected), key=lambda pair: pair[0])
    if remaining:
        start = slot % len(remaining)
        for offset in range(min(2, len(remaining))):
            add(remaining[(start + offset) % len(remaining)])
    for pair in ranked:
        add(pair)
    return selected[:count]


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
        futures = {executor.submit(client.get_history, code, start_ms, end_ms): code for code in codes}
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
    if short.quality_reasons:
        details = ", ".join(
            f"{window}m={reason}" for window, reason in sorted(short.quality_reasons.items())
        )
        print(f"Datenhinweis {display}: {details}", file=sys.stderr)
    if short.reversal_guard:
        print(
            f"Trendwechsel-Schutz {display}: Bestätigung fehlt "
            f"(Achse={short.temporal_score:+.3f}, "
            f"Streak={short.positive_streak}/{short.negative_streak}).",
            file=sys.stderr,
        )


def log_weekday_context(display: str, context: dict[str, Any]) -> None:
    diagnostics = context.get("weekday_diagnostics") or {}
    top = diagnostics.get("top") or []
    detail = " | ".join(
        f"{item.get('day')} q={float(item.get('score', 0.0)):.3f} "
        f"c={float(item.get('confidence', 0.0)):.2f}"
        f"{' ✓' if item.get('qualified') else ''}"
        for item in top
    ) or "keine belastbaren Kandidaten"
    raw = ''.join(diagnostics.get("raw") or []) or "—"
    stable = ''.join(diagnostics.get("stable") or []) or "—"
    print(
        f"Wochentage {display}: Modus={diagnostics.get('mode', '?')} "
        f"Samples={diagnostics.get('samples', 0)} Roh={raw} Anzeige={stable} | {detail}"
    )


def _daily_retry_due(entry: dict[str, Any] | None, now: datetime, config: dict[str, Any]) -> bool:
    """Retry early failures quickly, then apply a small credit-saving backoff."""
    if not entry:
        return True
    attempts = int(entry.get("attempt_count", 0))
    immediate_attempts = int(config.get("daily_retry_immediate_attempts", 3))
    if attempts < immediate_attempts:
        return True
    raw_last = entry.get("last_attempt_at")
    if not raw_last:
        return True
    try:
        last = datetime.fromisoformat(str(raw_last))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    wait_minutes = int(config.get("daily_retry_minutes", 20))
    return now - last >= timedelta(minutes=wait_minutes)


def refresh_daily_state_if_needed(
    *,
    client: LiveCoinWatchClient,
    resolved_all: list[tuple[str, str]],
    now: datetime,
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[str], int, bool]:
    """Refresh only missing/incomplete daily contexts and persist every improvement.

    GitHub caches are immutable. The workflow therefore restores the latest
    matching state and saves each changed state under a unique run key. A failed
    coin remains retryable; a valid calculation with zero positive weekdays is
    considered complete and is not queried again that day.
    """
    timezone_name = str(config.get("timezone", "Europe/Berlin"))
    today = local_day_key(now, timezone_name)
    previous = load_state(DAILY_STATE_PATH)
    before_fingerprint = state_fingerprint(previous)
    previous_coins = previous.get("coins") if isinstance(previous.get("coins"), dict) else {}
    current_codes = {display: api for display, api in resolved_all}

    compatible = (
        previous.get("version") == STATE_VERSION
        and previous.get("revision") == STATE_REVISION
    )
    refresh_displays: list[str] = []
    for display in current_codes:
        entry = previous_coins.get(display)
        if compatible and context_is_complete(entry, today):
            continue
        if not compatible or _daily_retry_due(entry if isinstance(entry, dict) else None, now, config):
            refresh_displays.append(display)

    if not refresh_displays:
        complete = sum(
            context_is_complete(previous_coins.get(display), today) for display in current_codes
        )
        print(
            f"Tageskontext {today}: {complete}/{len(current_codes)} vollständig; "
            "keine Langzeitabfrage fällig."
        )
        return previous, [], 0, False

    analysis_timezone = ZoneInfo(timezone_name)
    local_midnight = now.astimezone(analysis_timezone).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    long_end = local_midnight.astimezone(timezone.utc)
    history_days = int(config.get("daily_history_days", 400))
    long_start = long_end - timedelta(days=history_days)
    display_to_api = {display: current_codes[display] for display in refresh_displays}
    codes = list(dict.fromkeys(display_to_api.values()))
    print(
        f"Tageskontext {today}: {len(codes)} fehlende/ungültige Langzeithistorien "
        f"({history_days} Tage bis {long_end.isoformat()}) ..."
    )
    histories, transport_failures = refresh_histories(
        client=client,
        codes=codes,
        start_ms=int(long_start.timestamp() * 1000),
        end_ms=int(long_end.timestamp() * 1000),
        workers=int(config.get("daily_history_parallel_requests", 3)),
        label="Tageskontext",
    )

    new_coins: dict[str, Any] = dict(previous_coins)
    failures: list[str] = list(transport_failures)
    successes = 0
    for display in refresh_displays:
        api_code = current_codes[display]
        prior = previous_coins.get(display) if isinstance(previous_coins, dict) else None
        prior_dict = prior if isinstance(prior, dict) else None
        same_target = prior_dict and (
            prior_dict.get("target_date") == today or prior_dict.get("computed_for") == today
        )
        attempt_count = int(prior_dict.get("attempt_count", 0)) + 1 if same_target else 1
        history = histories.get(api_code)
        if history:
            try:
                new_coins[display] = build_daily_coin_context(
                    display=display,
                    api_code=api_code,
                    history=history,
                    now=now,
                    timezone=timezone_name,
                    config=config,
                    previous=prior_dict,
                    computed_for=today,
                    attempt_count=attempt_count,
                )
                successes += 1
            except InsufficientDailyHistory as exc:
                reason = str(exc)
                failures.append(api_code)
                new_coins[display] = carry_forward_context(
                    display=display,
                    api_code=api_code,
                    previous=prior_dict,
                    computed_for=today,
                    now=now,
                    reason=reason,
                    attempt_count=attempt_count,
                )
                print(f"WARNUNG: Tageskontext {display}: {reason}; erneuter Versuch folgt.", file=sys.stderr)
        else:
            reason = "LCW-Langzeithistorie nicht verfügbar"
            new_coins[display] = carry_forward_context(
                display=display,
                api_code=api_code,
                previous=prior_dict,
                computed_for=today,
                now=now,
                reason=reason,
                attempt_count=attempt_count,
            )
        log_weekday_context(display, new_coins[display])

    # Remove deleted coins; keep successful/stale contexts for all configured coins.
    new_coins = {display: new_coins[display] for display in current_codes if display in new_coins}
    complete_count = sum(context_is_complete(new_coins.get(display), today) for display in current_codes)
    pending = [display for display in current_codes if not context_is_complete(new_coins.get(display), today)]
    state = {
        "version": STATE_VERSION,
        "revision": STATE_REVISION,
        "date": today,
        "generated_at": now.isoformat(),
        "timezone": timezone_name,
        "coins": new_coins,
        "complete_count": complete_count,
        "pending": pending,
        "failures": sorted(set(failures)),
    }
    save_state(DAILY_STATE_PATH, state)
    changed = state_fingerprint(state) != before_fingerprint
    if changed:
        DAILY_SAVE_MARKER.parent.mkdir(parents=True, exist_ok=True)
        DAILY_SAVE_MARKER.write_text(
            f"{today} complete={complete_count}/{len(current_codes)} successes={successes}\n",
            encoding="utf-8",
        )
    print(
        f"Tageskontext gespeichert: {complete_count}/{len(current_codes)} vollständig, "
        f"{len(pending)} offen."
    )
    return state, sorted(set(failures)), len(codes), changed


def _short_is_displayable(short: ShortMetrics) -> bool:
    """Only complete 10/20/60-minute analyses may enter the visible Top 8."""
    if short.data_quality == "insufficient":
        return False
    return all(short.window_setup_scores.get(window) is not None for window in (10, 20, 60))


def _build_short_for_pair(
    *,
    display: str,
    api_code: str,
    current_by_code: dict[str, dict[str, Any]],
    histories: dict[str, list],
    now_ms: int,
    btc_short: ShortMetrics,
    config: dict[str, Any],
) -> ShortMetrics:
    short = build_short_metrics(
        current=current_by_code[api_code],
        short_history=histories.get(api_code, []),
        now_ms=now_ms,
        btc_price_changes=btc_short.price_changes,
        config=config,
        is_reference=False,
        btc_short=btc_short,
    )
    log_quality(display, short)
    return short

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

    # A restored marker belongs to an older immutable cache; only this run may recreate it.
    DAILY_SAVE_MARKER.unlink(missing_ok=True)

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

    resolved_all = [resolved_reference, *resolved_pool]
    daily_state, daily_failures, daily_request_count, _daily_changed = refresh_daily_state_if_needed(
        client=client,
        resolved_all=resolved_all,
        now=now,
        config=config,
    )

    top_count = int(config.get("top_coin_count", 8))
    initial_count = max(top_count, int(config.get("preselect_coin_count", 22)))
    candidate_order = balanced_preselection(
        resolved_pool,
        current_by_code,
        reference_current,
        len(resolved_pool),
        slot=now_ms // (5 * 60_000),
    )

    short_minutes = int(config.get("short_history_minutes", 720))
    short_start_ms = int((now - timedelta(minutes=short_minutes)).timestamp() * 1000)
    short_histories: dict[str, list] = {}
    short_failures: list[str] = []
    attempted_pairs: list[tuple[str, str]] = []
    short_by_code: dict[str, ShortMetrics] = {}
    short_request_count = 0

    def load_short_batch(pairs: list[tuple[str, str]], *, include_reference: bool = False) -> None:
        nonlocal short_request_count
        fresh_pairs = [pair for pair in pairs if pair not in attempted_pairs]
        attempted_pairs.extend(fresh_pairs)
        codes = [api for _, api in fresh_pairs]
        if include_reference and reference_api not in short_histories:
            codes.insert(0, reference_api)
        codes = list(dict.fromkeys(codes))
        if not codes:
            return
        print(f"Lade frische Kurzzeithistorien für {len(codes)} Coins ({short_minutes} Min) ...")
        histories, failures = refresh_histories(
            client=client,
            codes=codes,
            start_ms=short_start_ms,
            end_ms=now_ms,
            workers=int(config.get("history_parallel_requests", 5)),
            label="Kurzzeit",
        )
        short_request_count += len(codes)
        short_histories.update(histories)
        short_failures.extend(failures)

    initial_pairs = candidate_order[:initial_count]
    load_short_batch(initial_pairs, include_reference=True)

    btc_short = build_short_metrics(
        current=reference_current,
        short_history=short_histories.get(reference_api, []),
        now_ms=now_ms,
        btc_price_changes=None,
        config=config,
        is_reference=True,
    )
    log_quality(reference_display, btc_short)
    if not _short_is_displayable(btc_short):
        # One isolated serial retry prevents a transient history error from producing
        # a misleading white BTC line. No Discord report is sent if it remains invalid.
        print("WARNUNG: BTC-Kurzzeitdaten unvollständig; serieller Sicherheitsversuch ...", file=sys.stderr)
        retry_histories, retry_failures = refresh_histories(
            client=client,
            codes=[reference_api],
            start_ms=int((now - timedelta(minutes=max(short_minutes, 1440))).timestamp() * 1000),
            end_ms=now_ms,
            workers=1,
            label="Kurzzeit-Retry",
        )
        short_request_count += 1
        short_histories.update(retry_histories)
        short_failures.extend(retry_failures)
        btc_short = build_short_metrics(
            current=reference_current,
            short_history=short_histories.get(reference_api, []),
            now_ms=now_ms,
            btc_price_changes=None,
            config=config,
            is_reference=True,
        )
        log_quality(reference_display, btc_short)
    if not _short_is_displayable(btc_short):
        raise RuntimeError("BTC-Kurzzeitdaten sind nicht vollständig; Bericht aus Sicherheitsgründen verworfen.")

    def analyze_new_pairs(pairs: list[tuple[str, str]]) -> None:
        for display, api_code in pairs:
            if api_code in short_by_code:
                continue
            short_by_code[api_code] = _build_short_for_pair(
                display=display,
                api_code=api_code,
                current_by_code=current_by_code,
                histories=short_histories,
                now_ms=now_ms,
                btc_short=btc_short,
                config=config,
            )

    analyze_new_pairs(initial_pairs)
    valid_pairs = [
        pair
        for pair in attempted_pairs
        if pair[1] in short_by_code and _short_is_displayable(short_by_code[pair[1]])
    ]
    # If LCW omitted one or more detail histories, progressively query the next
    # map-ranked coins. This normally costs nothing extra and avoids white Top-8 lines.
    cursor = initial_count
    fallback_batch_size = max(1, int(config.get("short_fallback_batch_size", 6)))
    while len(valid_pairs) < top_count and cursor < len(candidate_order):
        batch = candidate_order[cursor : cursor + fallback_batch_size]
        cursor += len(batch)
        load_short_batch(batch)
        analyze_new_pairs(batch)
        valid_pairs = [
            pair
            for pair in attempted_pairs
            if pair[1] in short_by_code and _short_is_displayable(short_by_code[pair[1]])
        ]

    if len(valid_pairs) < top_count:
        raise RuntimeError(
            f"Nur {len(valid_pairs)}/{top_count} Coins besitzen vollständige 10/20/60-Minuten-Daten; "
            "Bericht aus Qualitätsgründen verworfen."
        )

    common = {
        "now": now,
        "timezone": str(config.get("timezone", "Europe/Berlin")),
        "block_hours": int(config.get("time_block_hours", 4)),
        "min_samples": int(config.get("seasonality_min_samples", 24)),
        "minimum_observations": int(config.get("seasonality_min_observations", 240)),
        "config": config,
    }
    ref_seasonality, ref_week_returns = context_for_coin(daily_state, reference_display)
    reference_analysis = build_coin_analysis(
        display_code=reference_display,
        api_code=reference_api,
        current=reference_current,
        short=btc_short,
        history=[],
        is_reference=True,
        seasonality_override=ref_seasonality,
        week_samples_override=ref_week_returns,
        map_flash_score=pre_anomaly_score(reference_current, reference_current),
        **common,
    )

    candidate_analyses: list[CoinAnalysis] = []
    for display, api_code in valid_pairs:
        seasonality, week_returns = context_for_coin(daily_state, display)
        candidate_analyses.append(
            build_coin_analysis(
                display_code=display,
                api_code=api_code,
                current=current_by_code[api_code],
                short=short_by_code[api_code],
                history=[],
                is_reference=False,
                seasonality_override=seasonality,
                week_samples_override=week_returns,
                map_flash_score=pre_anomaly_score(current_by_code[api_code], reference_current),
                **common,
            )
        )

    top_analyses = sorted(candidate_analyses, key=confidence_sort_key, reverse=True)[:top_count]
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
                "version": "3.2.6",
                "revision": STATE_REVISION,
                "generated_at": now.isoformat(),
                "daily_context_date": daily_state.get("date"),
                "daily_context_generated_at": daily_state.get("generated_at"),
                "daily_context_complete": daily_state.get("complete_count"),
                "daily_context_pending": daily_state.get("pending", []),
                "reference": analysis_to_dict(reference_analysis),
                "top_coins": [analysis_to_dict(item) for item in top_analyses],
                "detail_attempted": [display for display, _ in attempted_pairs],
                "detail_valid": [display for display, _ in valid_pairs],
                "unresolved": unresolved,
                "short_history_failures": sorted(set(short_failures)),
                "daily_context_failures": daily_failures,
                "api_requests_expected": 1 + short_request_count + daily_request_count,
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
