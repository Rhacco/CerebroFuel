"""Entry point for crypto-signal-monitor v3.3.0 volume-priority ranking."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

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
    build_daily_contexts,
    context_for_coin,
    history_from_context,
    load_state,
    local_day_key,
    save_state,
    volume_trend_from_context,
)
from discord_sender import send_discord
from lcw_client import LiveCoinWatchClient
from flash_state import update_and_score
from ranking_context import (
    btc_performance_context,
    combined_priority,
    seven_day_volume_context,
    small_cap_bonuses,
)

APP_VERSION = "3.3.0"
ROOT = Path(__file__).resolve().parent
DAILY_STATE_PATH = ROOT / ".cache" / "seasonality" / "state.json"
CHANGED_FLAG = ROOT / ".cache" / "seasonality" / "changed.flag"
FLASH_STATE_PATH = ROOT / ".cache" / "flash" / "state.json"


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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daily-only", action="store_true")
    mode.add_argument("--monitor-only", action="store_true")
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
    current_by_code: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str] | None:
    display, candidates = pair
    for candidate in candidates:
        if candidate in current_by_code:
            return display, candidate
    return None


def _turnover_pct(row: Mapping[str, Any]) -> float:
    volume = max(float(row.get("volume") or 0.0), 0.0)
    cap = max(float(row.get("cap") or 0.0), 0.0)
    return volume / cap * 100.0 if cap > 0 else 0.0


def balanced_preselection(
    pool: list[tuple[str, str]],
    current_by_code: Mapping[str, Mapping[str, Any]],
    reference_current: Mapping[str, Any],
    count: int,
    slot: int,
) -> list[tuple[str, str]]:
    ranked = sorted(
        pool,
        key=lambda pair: pre_anomaly_score(current_by_code[pair[1]], reference_current),
        reverse=True,
    )
    selected: list[tuple[str, str]] = []

    def add(pair: tuple[str, str]) -> None:
        if pair not in selected and len(selected) < count:
            selected.append(pair)

    for pair in ranked[: max(10, count - 5)]:
        add(pair)
    for pair in sorted(pool, key=lambda pair: _turnover_pct(current_by_code[pair[1]]), reverse=True):
        if len(selected) >= count - 2:
            break
        add(pair)
    remaining = sorted((pair for pair in pool if pair not in selected), key=lambda pair: pair[0])
    if remaining:
        start = slot % len(remaining)
        for offset in range(min(2, len(remaining))):
            add(remaining[(start + offset) % len(remaining)])
    for pair in ranked:
        add(pair)
    return selected[:count]


def _new_client(api_key: str, config: Mapping[str, Any]) -> LiveCoinWatchClient:
    return LiveCoinWatchClient(
        api_key=api_key,
        currency=str(config.get("currency", "USD")),
        timeout=int(config.get("request_timeout_seconds", 25)),
        request_interval_seconds=float(config.get("request_interval_seconds", 0.30)),
        burst_limit=int(config.get("request_burst_limit", 32)),
        burst_window_seconds=float(config.get("request_burst_window_seconds", 60)),
        rate_state_path=os.getenv("LCW_RATE_STATE_PATH", str(ROOT / ".cache" / "lcw-rate.json")),
    )


def _merge_points(*series: list) -> list:
    merged = {}
    for points in series:
        for point in points:
            merged[point.timestamp_ms] = point
    return [merged[key] for key in sorted(merged)]


def _set_changed(changed: bool) -> None:
    CHANGED_FLAG.parent.mkdir(parents=True, exist_ok=True)
    if changed:
        CHANGED_FLAG.write_text("changed\n", encoding="utf-8")
    else:
        CHANGED_FLAG.unlink(missing_ok=True)
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")


def _log_weekday_context(display: str, context: Mapping[str, Any]) -> None:
    diagnostics = context.get("weekday_diagnostics") or {}
    top = diagnostics.get("top") or []
    detail = " | ".join(
        f"{item.get('day')} q={float(item.get('score', 0.0)):.4f} "
        f"c={float(item.get('confidence', 0.0)):.2f}"
        f"{' ✓' if item.get('selected') else ''}"
        for item in top[:4]
    ) or "keine positiven Kandidaten"
    raw = "".join(diagnostics.get("raw") or []) or "—"
    stable = "".join(diagnostics.get("stable") or []) or "—"
    print(
        f"Wochentage {display}: Wochen={diagnostics.get('complete_weeks', 0)} "
        f"Roh={raw} Anzeige={stable} Beta={float(diagnostics.get('market_beta', 0.0)):.2f} "
        f"Breite={float(diagnostics.get('market_breadth', 0.0)):.0f} | {detail}"
    )


def prepare_daily_context(config: dict[str, Any], api_key: str) -> int:
    """Prepare the exact v3.3.0 daily cache before any Discord message.

    A current v3.2.7 cache is migrated locally from its stored raw histories.
    No long LCW requests are needed for that migration. On later calendar days,
    cached histories are updated with one recent request per existing coin.
    """
    reference_pair, pool_pairs = parse_layout(config)
    all_pairs = [reference_pair, *pool_pairs]
    expected = [display for display, _ in all_pairs]
    now = datetime.now(timezone.utc)
    timezone_name = str(config.get("timezone", "Europe/Berlin"))
    today = local_day_key(now, timezone_name)
    previous = load_state(DAILY_STATE_PATH)
    previous_coins = previous.get("coins") if isinstance(previous.get("coins"), dict) else {}

    exact = (
        previous.get("version") == STATE_VERSION
        and previous.get("revision") == STATE_REVISION
        and previous.get("date") == today
        and all(display in previous_coins for display in expected)
    )
    if exact:
        print(
            f"Tageskontext {today}: exakter v3.3.0-Cache, 0 Langzeitabfragen "
            f"({len(expected)} Coins)."
        )
        _set_changed(False)
        return 0

    # Reuse every raw history stored by previous v3.2.7 revisions. This is the
    # critical migration path that avoids a 100+ request rebuild.
    histories: dict[str, list] = {}
    api_codes: dict[str, str] = {}
    missing_pairs: list[tuple[str, tuple[str, ...]]] = []
    for display, candidates in all_pairs:
        old = previous_coins.get(display) if isinstance(previous_coins, dict) else None
        cached = history_from_context(old if isinstance(old, Mapping) else None)
        old_code = str((old or {}).get("api_code") or "").upper() if isinstance(old, Mapping) else ""
        if cached:
            histories[display] = cached
            api_codes[display] = old_code or candidates[0]
        else:
            missing_pairs.append((display, candidates))

    same_calendar_day = previous.get("date") == today
    client: LiveCoinWatchClient | None = None
    request_count = 0
    failures: list[str] = []

    def client_instance() -> LiveCoinWatchClient:
        nonlocal client
        if client is None:
            client = _new_client(api_key, config)
        return client

    # If this is a new day, increment all cached histories once. If it is merely
    # an algorithm migration on the same day, recompute locally with zero LCW calls.
    if not same_calendar_day and histories:
        from zoneinfo import ZoneInfo

        local_midnight = now.astimezone(ZoneInfo(timezone_name)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_ms = int(local_midnight.astimezone(timezone.utc).timestamp() * 1000)
        incremental_days = int(config.get("daily_incremental_days", 12))
        keep_days = int(config.get("daily_history_days", 300)) + 21
        minimum_ms = end_ms - keep_days * 86_400_000
        for display in list(histories):
            cached = histories[display]
            code = api_codes[display]
            start_ms = max(
                minimum_ms,
                cached[-1].timestamp_ms - incremental_days * 86_400_000,
            )
            try:
                raw = client_instance().get_history(code, start_ms, end_ms, allow_empty=True)
                request_count += 1
                fresh = normalize_history(raw)
                histories[display] = [
                    point
                    for point in _merge_points(cached, fresh)
                    if point.timestamp_ms >= minimum_ms
                ]
            except Exception as exc:
                failures.append(display)
                print(
                    f"Tageskontext {display}: letzter gültiger Rohverlauf bleibt aktiv ({exc}).",
                    file=sys.stderr,
                )

    # Only genuinely missing/new coins need a map lookup and chunked bootstrap.
    if missing_pairs:
        candidate_codes = list(dict.fromkeys(code for _, codes in missing_pairs for code in codes))
        current_by_code = client_instance().get_coins(candidate_codes)
        request_count += 1
        from zoneinfo import ZoneInfo

        local_midnight = now.astimezone(ZoneInfo(timezone_name)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_ms = int(local_midnight.astimezone(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - (int(config.get("daily_history_days", 300)) + 2) * 86_400_000
        for display, candidates in missing_pairs:
            resolved = next((code for code in candidates if code in current_by_code), None)
            if resolved is None:
                failures.append(display)
                histories[display] = []
                api_codes[display] = candidates[0]
                continue
            api_codes[display] = resolved
            try:
                raw, used, _ = client_instance().get_history_chunked(
                    resolved,
                    start_ms,
                    end_ms,
                    chunk_days=int(config.get("daily_history_chunk_days", 100)),
                )
                request_count += used
                histories[display] = normalize_history(raw)
            except Exception as exc:
                failures.append(display)
                histories[display] = []
                print(f"Tageskontext {display}: noch keine nutzbare Historie ({exc}).", file=sys.stderr)

    # Empty histories remain valid empty contexts; they do not force repeated
    # requests within the day. The monitor can still show these coins via flash data.
    for display, candidates in all_pairs:
        histories.setdefault(display, [])
        api_codes.setdefault(display, candidates[0])

    use_hysteresis = previous.get("revision") == STATE_REVISION
    new_coins = build_daily_contexts(
        histories=histories,
        api_codes=api_codes,
        reference_display=reference_pair[0],
        now=now,
        timezone=timezone_name,
        config=config,
        previous_coins=previous_coins,
        computed_for=today,
        use_previous_hysteresis=use_hysteresis,
    )
    for display in expected:
        _log_weekday_context(display, new_coins[display])

    state = {
        "version": STATE_VERSION,
        "revision": STATE_REVISION,
        "date": today,
        "generated_at": now.isoformat(),
        "timezone": timezone_name,
        "coins": new_coins,
        "complete_count": len(new_coins),
        "failures": sorted(set(failures)),
        "migrated_from_version": previous.get("version"),
        "migrated_from_revision": previous.get("revision"),
        "long_requests": request_count,
    }
    save_state(DAILY_STATE_PATH, state)
    visible = sum(bool(item.get("stable_best_weekdays")) for item in new_coins.values())
    source = "lokale Cache-Migration" if same_calendar_day and request_count == 0 else "Tagesaktualisierung"
    print(
        f"Tageskontext {today}: {source}; {visible}/{len(new_coins)} Coins mit Top-Wochentag; "
        f"{request_count} Langzeitabfragen."
    )
    _set_changed(True)
    return 0


def refresh_histories(
    *,
    client: LiveCoinWatchClient,
    codes: list[str],
    start_ms: int,
    end_ms: int,
    label: str,
) -> tuple[dict[str, list], list[str]]:
    histories: dict[str, list] = {}
    failed: list[str] = []
    for code in codes:
        try:
            histories[code] = normalize_history(client.get_history(code, start_ms, end_ms))
            print(f"{label}: {code} ({len(histories[code])} Punkte)")
        except Exception as exc:
            failed.append(code)
            print(f"HINWEIS: {label} {code} nicht verfügbar: {exc}", file=sys.stderr)
    return histories, failed


def log_quality(display: str, short: ShortMetrics) -> None:
    if short.quality_reasons:
        details = ", ".join(
            f"{window}m={reason}" for window, reason in sorted(short.quality_reasons.items())
        )
        print(f"Datenhinweis {display}: {details}", file=sys.stderr)
    if short.reversal_guard:
        print(
            f"Trendwechsel-Schutz {display}: Achse={short.temporal_score:+.3f}, "
            f"Streak={short.positive_streak}/{short.negative_streak}.",
            file=sys.stderr,
        )


def _short_is_displayable(short: ShortMetrics) -> bool:
    if short.data_quality == "insufficient":
        return False
    return all(short.window_setup_scores.get(window) is not None for window in (10, 30, 60))


def _build_short_for_pair(
    *,
    display: str,
    api_code: str,
    current_by_code: Mapping[str, Mapping[str, Any]],
    histories: Mapping[str, list],
    now_ms: int,
    btc_short: ShortMetrics,
    config: Mapping[str, Any],
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


def run_monitor(config: dict[str, Any], api_key: str, webhook_url: str, should_send: bool) -> int:
    reference_pair, pool_pairs = parse_layout(config)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    today = local_day_key(now, str(config.get("timezone", "Europe/Berlin")))
    daily_state = load_state(DAILY_STATE_PATH)
    if not (
        daily_state.get("version") == STATE_VERSION
        and daily_state.get("revision") == STATE_REVISION
        and daily_state.get("date") == today
        and isinstance(daily_state.get("coins"), dict)
    ):
        raise RuntimeError("Kein aktueller kompatibler Tageskontext vorhanden; Versand wird verhindert.")
    print(
        f"Tageskontext: exact-current {STATE_REVISION}, "
        f"0 Langzeitabfragen im Monitorlauf."
    )

    all_pairs = [reference_pair, *pool_pairs]
    candidate_codes = list(dict.fromkeys(code for _, codes in all_pairs for code in codes))
    client = _new_client(api_key, config)
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
        print("HINWEIS: Aktuell nicht von LCW aufgelöst: " + ", ".join(unresolved), file=sys.stderr)

    # One fresh /coins/map response checks the complete configured pool. The
    # persisted map snapshots produce true 10/30/60-minute flash scores for every
    # coin without spending one additional LCW credit.
    flash_signals, flash_stats = update_and_score(
        path=FLASH_STATE_PATH,
        resolved_pairs=[resolved_reference, *resolved_pool],
        current_by_code=current_by_code,
        reference_display=reference_display,
        reference_api_code=reference_api,
        now_ms=now_ms,
        config=config,
    )
    print(
        f"Flash-Vollscan: {flash_stats.get('coins', 0)} Coins, "
        f"{flash_stats.get('full_windows', 0)} mit 10/30/60m, "
        f"Abdeckung={float(flash_stats.get('coverage', 0.0)) * 100:.0f}% "
        f"(0 zusätzliche LCW-Requests)."
    )

    # Secondary context is calculated for the whole pool before detail selection.
    # None of these bounded bonuses can overpower the primary 30-minute gap.
    rows_by_display = {
        display: current_by_code[api_code]
        for display, api_code in [resolved_reference, *resolved_pool]
    }
    raw_volume_7d: dict[str, float | None] = {}
    for display, api_code in [resolved_reference, *resolved_pool]:
        raw_volume = current_by_code[api_code].get("volume")
        current_volume = None if raw_volume in (None, "") else float(raw_volume)
        raw_volume_7d[display] = volume_trend_from_context(
            daily_state,
            display,
            current_volume=current_volume,
            now_ms=now_ms,
            days=7,
        )
    volume_7d_context = seven_day_volume_context(raw_volume_7d)
    cap_bonuses = small_cap_bonuses(
        rows_by_display,
        minimum_reliable_volume=float(config.get("minimum_reliable_volume_usd", 500_000)),
    )
    btc_context = {
        display: btc_performance_context(
            current_by_code[api_code],
            reference_current,
            is_reference=(display == reference_display),
        )
        for display, api_code in [resolved_reference, *resolved_pool]
    }

    top_count = int(config.get("top_coin_count", 8))
    initial_count = max(top_count, int(config.get("preselect_coin_count", 22)))
    max_detail_requests = max(initial_count, int(config.get("max_short_detail_requests", 26)))

    def flash_order_key(pair: tuple[str, str]) -> tuple[float, float, float, float, str]:
        display, api_code = pair
        signal = flash_signals.get(display)
        fallback = pre_anomaly_score(current_by_code[api_code], reference_current)
        primary = max(signal.score if signal else 0.0, fallback * 0.45)
        volume_context = volume_7d_context.get(display) or {}
        priority = combined_priority(
            primary_gap_score=primary,
            volume_7d_bonus=float(volume_context.get("bonus") or 0.0),
            market_cap_bonus=float(cap_bonuses.get(display, 0.0)),
            volatility_score=float(signal.volatility_score if signal else 0.0),
            recovery_score=float(signal.recovery_score if signal else 0.0),
            quality=float(signal.quality if signal else 0.35),
        )
        return (
            float(int(primary // 8.0)),
            priority,
            primary,
            float(signal.quality if signal else 0.0),
            display,
        )

    candidate_order = sorted(resolved_pool, key=flash_order_key, reverse=True)

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
        print("HINWEIS: BTC-Kurzzeitdaten unvollständig; Sicherheitsversuch ...", file=sys.stderr)
        retry, failures = refresh_histories(
            client=client,
            codes=[reference_api],
            start_ms=int((now - timedelta(minutes=max(short_minutes, 1440))).timestamp() * 1000),
            end_ms=now_ms,
            label="Kurzzeit-Retry",
        )
        short_request_count += 1
        short_histories.update(retry)
        short_failures.extend(failures)
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
        raise RuntimeError("BTC-Kurzzeitdaten unvollständig; Bericht verworfen.")

    def analyze_pairs(pairs: list[tuple[str, str]]) -> None:
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

    analyze_pairs(initial_pairs)
    valid_pairs = [
        pair for pair in attempted_pairs
        if pair[1] in short_by_code and _short_is_displayable(short_by_code[pair[1]])
    ]
    cursor = initial_count
    batch_size = max(1, int(config.get("short_fallback_batch_size", 6)))
    while (
        len(valid_pairs) < top_count
        and cursor < len(candidate_order)
        and len(attempted_pairs) < max_detail_requests
    ):
        remaining_budget = max_detail_requests - len(attempted_pairs)
        batch = candidate_order[cursor : cursor + min(batch_size, remaining_budget)]
        cursor += len(batch)
        load_short_batch(batch)
        analyze_pairs(batch)
        valid_pairs = [
            pair for pair in attempted_pairs
            if pair[1] in short_by_code and _short_is_displayable(short_by_code[pair[1]])
        ]
    if len(valid_pairs) < top_count:
        raise RuntimeError(
            f"Nur {len(valid_pairs)}/{top_count} Coins besitzen vollständige Kurzzeitdaten."
        )

    common = {
        "now": now,
        "timezone": str(config.get("timezone", "Europe/Berlin")),
        "block_hours": int(config.get("time_block_hours", 4)),
        "min_samples": int(config.get("seasonality_min_samples", 8)),
        "minimum_observations": int(config.get("seasonality_min_observations", 70)),
        "config": config,
    }
    def ranking_context_kwargs(display: str, api_code: str) -> dict[str, Any]:
        signal = flash_signals.get(display)
        volume_context = volume_7d_context.get(display) or {}
        btc24, btc24_color, btc7, btc7_color = btc_context[display]
        fallback = pre_anomaly_score(current_by_code[api_code], reference_current) * 0.45
        return {
            "map_flash_score": max(float(signal.score if signal else 0.0), fallback),
            "map_flash_direction": signal.direction if signal else "=",
            "map_volatility_score": float(signal.volatility_score if signal else 0.0),
            "map_recovery_score": float(signal.recovery_score if signal else 0.0),
            "map_recovery_color": signal.recovery_color if signal else "🟡",
            "volume_7d_pct": volume_context.get("pct"),
            "volume_7d_color": str(volume_context.get("color") or "⚪"),
            "volume_7d_bonus": float(volume_context.get("bonus") or 0.0),
            "btc_24h_pct": float(btc24),
            "btc_24h_color": btc24_color,
            "btc_7d_pct": float(btc7),
            "btc_7d_color": btc7_color,
            "market_cap_bonus": float(cap_bonuses.get(display, 0.0)),
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
        **ranking_context_kwargs(reference_display, reference_api),
        **common,
    )

    analyses: list[CoinAnalysis] = []
    for display, api_code in valid_pairs:
        seasonality, week_returns = context_for_coin(daily_state, display)
        analyses.append(
            build_coin_analysis(
                display_code=display,
                api_code=api_code,
                current=current_by_code[api_code],
                short=short_by_code[api_code],
                history=[],
                is_reference=False,
                seasonality_override=seasonality,
                week_samples_override=week_returns,
                **ranking_context_kwargs(display, api_code),
                **common,
            )
        )

    top_analyses = sorted(analyses, key=confidence_sort_key, reverse=True)[:top_count]
    for item in top_analyses:
        print(
            f"Priorität {item.display_code}: Gesamt={item.ranking_score:.2f} "
            f"Schere30={item.short.divergence_30 if item.short.divergence_30 is not None else 0.0:+.2f} "
            f"Primär={item.flash_score:.1f} V7={item.volume_7d_bonus:.1f} "
            f"Cap={item.market_cap_bonus:.1f} Volatilität={item.volatility_bonus:.1f} "
            f"Recovery={item.recovery_bonus:.1f}."
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
                "version": APP_VERSION,
                "cache_version": STATE_VERSION,
                "revision": STATE_REVISION,
                "generated_at": now.isoformat(),
                "daily_context_date": daily_state.get("date"),
                "daily_context_generated_at": daily_state.get("generated_at"),
                "reference": analysis_to_dict(reference_analysis),
                "top_coins": [analysis_to_dict(item) for item in top_analyses],
                "detail_attempted": [display for display, _ in attempted_pairs],
                "detail_valid": [display for display, _ in valid_pairs],
                "unresolved": unresolved,
                "short_history_failures": sorted(set(short_failures)),
                "flash_pool": {
                    display: signal.to_dict()
                    for display, signal in sorted(flash_signals.items())
                },
                "flash_stats": flash_stats,
                "volume_7d_context": volume_7d_context,
                "market_cap_bonuses": cap_bonuses,
                "btc_performance": {
                    display: {
                        "relative_24h_pct": values[0],
                        "relative_24h_color": values[1],
                        "relative_7d_pct": values[2],
                        "relative_7d_color": values[3],
                    }
                    for display, values in btc_context.items()
                },
                "api_requests_expected": 1 + short_request_count,
                "api_request_cap": 1 + max_detail_requests + 1,
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
            timeout=int(config.get("request_timeout_seconds", 25)),
        )
        print("Discord-Nachricht gesendet.")
    else:
        print("Testmodus: keine Discord-Nachricht gesendet.")
    return 0


def run() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    api_key = os.getenv("LCW_API_KEY", "").strip()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    should_send = env_bool("SEND_DISCORD", True) and not args.no_send and not args.daily_only
    if not api_key:
        raise ValueError("GitHub Secret LCW_API_KEY fehlt.")
    if should_send and not webhook_url:
        raise ValueError("GitHub Secret DISCORD_WEBHOOK_URL fehlt.")

    if args.daily_only:
        return prepare_daily_context(config, api_key)
    if not args.monitor_only:
        prepare_daily_context(config, api_key)
    return run_monitor(config, api_key, webhook_url, should_send)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
