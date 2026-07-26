"""Fresh v3.5 crypto category and short-swing monitor.

Fast runs use closed one-minute exchange candles. LiveCoinWatch is limited to
one full-pool map request plus durable long-term histories. Older cache versions
are intentionally ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from analysis import (
    BLUE,
    ORANGE,
    RED,
    WHITE,
    YELLOW,
    CoinAnalysis,
    Seasonality,
    ShortMetrics,
    analysis_to_dict,
    apply_opportunity_analysis,
    build_coin_analysis,
    build_report,
    normalize_history,
)
from category_context import build_category_context, format_category_line, select_category_entries
from category_state import STATE_VERSION as CATEGORY_STATE_VERSION, update_category_state
from daily_context import (
    STATE_REVISION,
    STATE_VERSION,
    build_daily_contexts,
    context_for_coin,
    history_from_context,
    load_state,
    local_day_key,
    save_state,
    target_profile_for_coin,
    volume_trend_from_context,
)
from discord_sender import send_discord
from flash_state import STATE_VERSION as FLASH_STATE_VERSION, update_and_score
from lcw_client import LiveCoinWatchClient
from market_data import IntradayMetrics, PublicMarketDataClient
from notification_state import mark_report_sent, report_send_decision
from opportunity import assess_opportunity, build_market_quality
from outcome_state import (
    STATE_VERSION as OUTCOME_STATE_VERSION,
    record_entry_candidates,
    update_and_resolve,
)
from ranking_context import btc_performance_context, seven_day_volume_context, small_cap_bonuses
from signal_state import STATE_VERSION as SIGNAL_STATE_VERSION, update_signal_states
from unlock_context import unlock_context

APP_VERSION = "3.5.0"
ROOT = Path(__file__).resolve().parent
CACHE_ROOT = ROOT / ".cache" / "v350"
LONGTERM_STATE_PATH = CACHE_ROOT / "longterm" / "state.json"
LONGTERM_BOOTSTRAP_PATH = CACHE_ROOT / "longterm" / "bootstrap.json"
LONGTERM_CHANGED_FLAG = CACHE_ROOT / "longterm" / "changed.flag"
FLASH_STATE_PATH = CACHE_ROOT / "runtime" / "flash.json"
OUTCOME_STATE_PATH = CACHE_ROOT / "runtime" / "outcomes.json"
CATEGORY_STATE_PATH = CACHE_ROOT / "runtime" / "categories.json"
SIGNAL_STATE_PATH = CACHE_ROOT / "runtime" / "signals.json"
NOTIFICATION_STATE_PATH = CACHE_ROOT / "runtime" / "notifications.json"


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config.json ist kein JSON-Objekt.")
    required = ["reference_coin", "groups", "categories", "currency", "timezone"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError("Fehlende config.json-Felder: " + ", ".join(missing))
    checks = {
        "schema_version": APP_VERSION,
        "quality_revision": STATE_REVISION,
        "flash_snapshot_version": FLASH_STATE_VERSION,
        "outcome_state_version": OUTCOME_STATE_VERSION,
        "category_state_version": CATEGORY_STATE_VERSION,
    }
    for key, expected in checks.items():
        if str(config.get(key)) != str(expected):
            raise ValueError(f"config.json {key}={config.get(key)!r}, erwartet {expected!r}.")
    signal_version = str((config.get("signal_state") or {}).get("state_version") or "")
    if signal_version != SIGNAL_STATE_VERSION:
        raise ValueError("config.json signal_state.state_version stimmt nicht mit signal_state.py überein.")
    return config


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() not in {"0", "false", "no", "off"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CF v3.5.0")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--force-discord", action="store_true")
    parser.add_argument("--longterm-only", action="store_true")
    parser.add_argument("--force-longterm", action="store_true")
    return parser.parse_args()


def parse_coin(item: Any) -> tuple[str, tuple[str, ...]]:
    if isinstance(item, str):
        code = item.upper().strip()
        return code, (code,)
    if isinstance(item, Mapping):
        display = str(item.get("display") or item.get("code") or "").upper().strip()
        raw_codes = item.get("codes")
        if isinstance(raw_codes, list):
            codes = tuple(str(code).upper().strip() for code in raw_codes if str(code).strip())
        else:
            code = str(item.get("code") or display).upper().strip()
            codes = (code,) if code else tuple()
        if display and codes:
            return display, tuple(dict.fromkeys(codes))
    raise ValueError(f"Ungültiger Coin-Eintrag: {item!r}")


def parse_layout(config: Mapping[str, Any]) -> tuple[tuple[str, tuple[str, ...]], list[tuple[str, tuple[str, ...]]]]:
    reference = parse_coin(config["reference_coin"])
    pool: list[tuple[str, tuple[str, ...]]] = []
    displays = {reference[0]}
    codes = {code: reference[0] for code in reference[1]}
    for group in config["groups"]:
        items = group.get("coins") if isinstance(group, Mapping) else group
        if not isinstance(items, list):
            raise ValueError("Jede Gruppe benötigt eine Coin-Liste.")
        for raw in items:
            display, candidates = parse_coin(raw)
            if display in displays:
                raise ValueError(f"Doppelter Coin: {display}")
            for code in candidates:
                if code in codes:
                    raise ValueError(f"Doppelter LCW-Code {code}: {codes[code]} und {display}")
                codes[code] = display
            displays.add(display)
            pool.append((display, candidates))
    owners: dict[str, str] = {}
    for category in config["categories"]:
        code = str(category.get("code") or "").upper()
        if not code or len(code) > 3:
            raise ValueError(f"Ungültige Kategorie {code!r}")
        for display in category.get("coins", []):
            name = str(display).upper()
            if name in owners:
                raise ValueError(f"Coin {name} doppelt kategorisiert.")
            owners[name] = code
    missing = sorted(displays - set(owners))
    unknown = sorted(set(owners) - displays)
    if missing or unknown:
        raise ValueError(f"Kategorie-/Pool-Abweichung: fehlend={missing}, unbekannt={unknown}")
    required = [str(x).upper() for x in (config.get("coin_selection") or {}).get("required_active", [])]
    absent = [name for name in required if name not in displays]
    if absent:
        raise ValueError("Pflichtcoins fehlen: " + ", ".join(absent))
    expected_count = int((config.get("coin_selection") or {}).get("unique_altcoin_count", len(pool)))
    if len(pool) != expected_count:
        raise ValueError(f"Altcoin-Anzahl {len(pool)} stimmt nicht mit {expected_count} überein.")
    return reference, pool


def resolve_pair(pair: tuple[str, tuple[str, ...]], rows: Mapping[str, Mapping[str, Any]]) -> tuple[str, str] | None:
    display, candidates = pair
    return next(((display, code) for code in candidates if code in rows), None)


def _new_client(api_key: str, config: Mapping[str, Any]) -> LiveCoinWatchClient:
    return LiveCoinWatchClient(
        api_key=api_key,
        currency=str(config.get("currency", "USD")),
        timeout=int(config.get("request_timeout_seconds", 25)),
        request_interval_seconds=float(config.get("request_interval_seconds", 0.30)),
        burst_limit=int(config.get("request_burst_limit", 32)),
        burst_window_seconds=float(config.get("request_burst_window_seconds", 60)),
        rate_state_path=os.getenv("LCW_RATE_STATE_PATH", str(CACHE_ROOT / "runtime" / "lcw-rate.json")),
    )


def _merge_points(*series: Sequence[Any]) -> list[Any]:
    merged: dict[int, Any] = {}
    for points in series:
        for point in points:
            merged[int(point.timestamp_ms)] = point
    return [merged[key] for key in sorted(merged)]


def _set_longterm_changed(changed: bool) -> None:
    LONGTERM_CHANGED_FLAG.parent.mkdir(parents=True, exist_ok=True)
    if changed:
        LONGTERM_CHANGED_FLAG.write_text("changed\n", encoding="utf-8")
    else:
        LONGTERM_CHANGED_FLAG.unlink(missing_ok=True)


def _load_bootstrap() -> dict[str, Any]:
    try:
        raw = json.loads(LONGTERM_BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "revision": STATE_REVISION, "coins": {}}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION or raw.get("revision") != STATE_REVISION:
        return {"version": STATE_VERSION, "revision": STATE_REVISION, "coins": {}}
    return raw


def _save_bootstrap(histories: Mapping[str, Sequence[Any]], api_codes: Mapping[str, str]) -> None:
    LONGTERM_BOOTSTRAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "revision": STATE_REVISION,
        "coins": {
            display: {
                "api_code": api_codes.get(display),
                "history": [[p.timestamp_ms, p.rate, p.volume] for p in points],
            }
            for display, points in histories.items()
        },
    }
    tmp = LONGTERM_BOOTSTRAP_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(LONGTERM_BOOTSTRAP_PATH)


def prepare_longterm_context(
    config: dict[str, Any],
    api_key: str,
    *,
    force: bool = False,
) -> tuple[int, dict[str, dict[str, Any]] | None]:
    """Build or refresh the fresh v3.5 LCW long-term cache.

    A missing first cache is built immediately. Progress is checkpointed so an
    interrupted bootstrap resumes instead of restarting. Old cache versions are
    never read.
    """
    reference, pool = parse_layout(config)
    pairs = [reference, *pool]
    expected = [display for display, _ in pairs]
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    timezone_name = str(config.get("timezone", "Europe/Berlin"))
    today = local_day_key(now, timezone_name)
    previous = load_state(LONGTERM_STATE_PATH)
    previous_coins = previous.get("coins") if isinstance(previous.get("coins"), dict) else {}
    all_present = all(display in previous_coins for display in expected)
    retry_after = int(previous.get("retry_after_ms") or 0)
    if (
        not force
        and previous.get("date") == today
        and all_present
        and (not previous.get("failures") or now_ms < retry_after)
    ):
        _set_longterm_changed(False)
        print(f"LCW-Langzeitcache {today}: aktuell ({len(expected)} Assets), 0 Historienabfragen.")
        return 0, None

    histories: dict[str, list[Any]] = {}
    api_codes: dict[str, str] = {}
    for display, candidates in pairs:
        old = previous_coins.get(display) if isinstance(previous_coins, dict) else None
        cached = history_from_context(old if isinstance(old, Mapping) else None)
        if cached:
            histories[display] = cached
            api_codes[display] = str((old or {}).get("api_code") or candidates[0]).upper()

    if not histories:
        bootstrap = _load_bootstrap()
        for display, raw in (bootstrap.get("coins") or {}).items():
            points = history_from_context(raw if isinstance(raw, Mapping) else None)
            if points:
                histories[str(display).upper()] = points
                api_codes[str(display).upper()] = str((raw or {}).get("api_code") or "").upper()

    client = _new_client(api_key, config)
    requests_used = 0
    failures: list[str] = []
    map_rows: dict[str, dict[str, Any]] | None = None
    missing = [(display, candidates) for display, candidates in pairs if not histories.get(display)]
    if missing:
        candidate_codes = list(dict.fromkeys(code for _, candidates in missing for code in candidates))
        map_rows = client.get_coins(candidate_codes)
        requests_used += 1
        local_midnight = now.astimezone(ZoneInfo(timezone_name)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_ms = int(local_midnight.astimezone(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - (int(config.get("daily_history_days", 300)) + 2) * 86_400_000
        for index, (display, candidates) in enumerate(missing, start=1):
            resolved = next((code for code in candidates if code in map_rows), None)
            api_codes[display] = resolved or candidates[0]
            if resolved is None:
                histories[display] = []
                failures.append(display)
            else:
                try:
                    raw, used, _ = client.get_history_chunked(
                        resolved,
                        start_ms,
                        end_ms,
                        chunk_days=int(config.get("daily_history_chunk_days", 100)),
                    )
                    requests_used += used
                    histories[display] = normalize_history(raw)
                except Exception as exc:
                    histories[display] = []
                    failures.append(display)
                    print(f"Langzeitaufbau {display}: {exc}", file=sys.stderr)
            if index % 5 == 0:
                _save_bootstrap(histories, api_codes)
    elif previous.get("date") != today or force:
        local_midnight = now.astimezone(ZoneInfo(timezone_name)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_ms = int(local_midnight.astimezone(timezone.utc).timestamp() * 1000)
        keep_after = end_ms - (int(config.get("daily_history_days", 300)) + 21) * 86_400_000
        incremental_days = int(config.get("daily_incremental_days", 12))
        for display in expected:
            points = histories.get(display, [])
            code = api_codes.get(display)
            if not points or not code:
                failures.append(display)
                continue
            start_ms = max(keep_after, points[-1].timestamp_ms - incremental_days * 86_400_000)
            try:
                raw = client.get_history(code, start_ms, end_ms, allow_empty=True)
                requests_used += 1
                histories[display] = [p for p in _merge_points(points, normalize_history(raw)) if p.timestamp_ms >= keep_after]
            except Exception as exc:
                failures.append(display)
                print(f"Langzeitupdate {display}: letzter gültiger Verlauf bleibt aktiv ({exc}).", file=sys.stderr)

    for display, candidates in pairs:
        histories.setdefault(display, [])
        api_codes.setdefault(display, candidates[0])

    new_coins = build_daily_contexts(
        histories=histories,
        api_codes=api_codes,
        reference_display=reference[0],
        now=now,
        timezone=timezone_name,
        config=config,
        previous_coins=previous_coins,
        computed_for=today,
        use_previous_hysteresis=bool(previous_coins),
    )
    state = {
        "version": STATE_VERSION,
        "revision": STATE_REVISION,
        "date": today,
        "generated_at": now.isoformat(),
        "timezone": timezone_name,
        "coins": new_coins,
        "complete_count": len(new_coins),
        "failures": sorted(set(failures)),
        "retry_after_ms": now_ms + (60 * 60_000 if failures else 0),
        "long_requests": requests_used,
    }
    save_state(LONGTERM_STATE_PATH, state)
    LONGTERM_BOOTSTRAP_PATH.unlink(missing_ok=True)
    _set_longterm_changed(True)
    usable = sum(bool(history_from_context(item)) for item in new_coins.values())
    print(f"LCW-Langzeitcache {today}: {usable}/{len(new_coins)} Historien, {requests_used} Requests, Fehler={len(set(failures))}.")
    return requests_used, map_rows


def _average(values: Sequence[float | None]) -> float | None:
    valid = [float(value) for value in values if value is not None]
    return sum(valid) / len(valid) if valid else None


def _short_from_sources(
    display: str,
    metrics: IntradayMetrics | None,
    flash: Any | None,
    btc_metrics: IntradayMetrics | None,
) -> ShortMetrics:
    metrics = metrics or IntradayMetrics(display=display)
    price_changes: dict[int, float | None] = {}
    volume_changes: dict[int, float | None] = {}
    volume_colors: dict[int, str] = {}
    window_quality: dict[int, str] = {}
    setup_scores: dict[int, float | None] = {}
    price_strengths: dict[int, float | None] = {}
    volume_strengths: dict[int, float | None] = {}
    for window in (10, 30, 60):
        price = metrics.price_changes.get(window)
        if price is None and flash is not None:
            if window == 10:
                price = _average([flash.price_changes.get(5), flash.price_changes.get(15)])
            else:
                price = flash.price_changes.get(window)
        ratio = metrics.volume_ratios.get(window)
        volume = None if ratio is None else (float(ratio) - 1.0) * 100.0
        if volume is None and flash is not None:
            if window == 10:
                volume = _average([flash.volume_changes.get(5), flash.volume_changes.get(15)])
            else:
                volume = flash.volume_changes.get(window)
        price_changes[window] = price
        volume_changes[window] = volume
        volume_colors[window] = metrics.volume_colors.get(window) or ("⚪" if volume is None else YELLOW)
        usable = price is not None and volume is not None
        window_quality[window] = "good" if usable and metrics.data_quality == "good" else ("uncertain" if usable else "insufficient")
        gap = None if not usable else float(volume) - float(price)
        setup_scores[window] = None if gap is None else min(100.0, abs(gap) * 10.0)
        price_strengths[window] = price
        volume_strengths[window] = None if ratio is None else float(ratio) - 1.0
    p30 = float(price_changes.get(30) or 0.0)
    btc30 = float((btc_metrics.price_changes.get(30) if btc_metrics else 0.0) or 0.0)
    relative = p30 - btc30
    relative_color = BLUE if relative >= 0.35 else (ORANGE if relative <= -0.35 else YELLOW)
    demand = float(metrics.demand_score)
    sell = float(metrics.sell_pressure_score)
    direction = "▲" if demand >= sell else "▼"
    color = BLUE if direction == "▲" else ORANGE
    quality = "good" if metrics.data_quality == "good" else ("uncertain" if metrics.data_quality == "partial" else "insufficient")
    if metrics.data_quality == "insufficient" and flash is not None and int(getattr(flash, "covered_windows", 0)) >= 2:
        quality = "uncertain"
    divergence30 = None
    if price_changes.get(30) is not None and volume_changes.get(30) is not None:
        divergence30 = float(volume_changes[30]) - float(price_changes[30])
    return ShortMetrics(
        price_changes=price_changes,
        volume_changes=volume_changes,
        volume_colors=volume_colors,
        relative_short_pct=relative,
        relative_color=relative_color,
        pressure_score=demand - sell,
        pressure_color=color,
        buy_count=min(8, max(1, int(round(demand / 12.5)))) if direction == "▲" else 0,
        sell_count=min(8, max(1, int(round(sell / 12.5)))) if direction == "▼" else 0,
        direction=direction,
        signal_color=color,
        anomaly_score=max(demand, sell),
        data_quality=quality,
        window_quality=window_quality,
        window_setup_scores=setup_scores,
        price_strengths=price_strengths,
        volume_strengths=volume_strengths,
        flash_score=max(demand, sell, float(getattr(flash, "score", 0.0) if flash else 0.0)),
        flash_direction=direction,
        divergence_30=divergence30,
        divergence_score=max(demand, sell),
        volatility_score=float(getattr(flash, "volatility_score", 0.0) if flash else 0.0),
        recovery_score=float(getattr(flash, "recovery_score", 0.0) if flash else 0.0),
        recovery_color=str(getattr(flash, "recovery_color", YELLOW) if flash else YELLOW),
        recent_crash_pct=float(getattr(flash, "recent_crash_pct", 0.0) if flash else 0.0),
    )


def _assessment_for(
    *,
    display: str,
    api_code: str,
    current_by_code: Mapping[str, Mapping[str, Any]],
    short_by_display: Mapping[str, ShortMetrics],
    flash_signals: Mapping[str, Any],
    intraday: Mapping[str, IntradayMetrics],
    reference_display: str,
    market_quality: Any,
    daily_state: Mapping[str, Any],
    live_profiles: Mapping[str, Mapping[str, Any]],
    unlocks: Mapping[str, Mapping[str, Any]],
    coin_categories: Mapping[str, Any],
    category_trends: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    category = coin_categories.get(display)
    category_code = str(category.category_code if category else "")
    trend = category_trends.get(category_code)
    result = assess_opportunity(
        display=display,
        current=current_by_code[api_code],
        short=short_by_display[display],
        flash_signal=flash_signals.get(display),
        intraday=intraday.get(display),
        btc_intraday=intraday.get(reference_display),
        market_quality=market_quality,
        historical_target=target_profile_for_coin(daily_state, display),
        live_target=live_profiles.get(display),
        unlock_penalty=float((unlocks.get(display) or {}).get("penalty") or 0.0),
        category_context=category.to_dict() if category else None,
        category_trend=trend.to_dict() if trend else None,
        event_penalty=float(category.event_penalty if category else 0.0),
        config=config,
    )
    return result.to_dict()


def run_monitor(
    config: dict[str, Any],
    api_key: str,
    webhook_url: str,
    *,
    should_send: bool,
    force_discord: bool,
    reused_map: dict[str, dict[str, Any]] | None = None,
) -> int:
    reference_pair, pool_pairs = parse_layout(config)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    daily_state = load_state(LONGTERM_STATE_PATH)
    if not daily_state or not isinstance(daily_state.get("coins"), dict):
        raise RuntimeError("Der frische v3.5-Langzeitcache konnte nicht aufgebaut werden.")

    all_pairs = [reference_pair, *pool_pairs]
    candidate_codes = list(dict.fromkeys(code for _, candidates in all_pairs for code in candidates))
    client = _new_client(api_key, config)
    reuse_complete = bool(
        reused_map
        and all(any(code in reused_map for code in candidates) for _, candidates in all_pairs)
    )
    current_by_code = reused_map if reuse_complete else None
    if current_by_code is None:
        current_by_code = client.get_coins(candidate_codes)
    resolved_reference = resolve_pair(reference_pair, current_by_code)
    if resolved_reference is None:
        raise RuntimeError("BTC fehlt in der LCW-Gesamt-Map.")
    reference_display, reference_api = resolved_reference
    resolved_pool: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for pair in pool_pairs:
        resolved = resolve_pair(pair, current_by_code)
        if resolved:
            resolved_pool.append(resolved)
        else:
            unresolved.append(pair[0])
    if unresolved:
        print("LCW nicht aufgelöst: " + ", ".join(unresolved), file=sys.stderr)

    resolved_all = [resolved_reference, *resolved_pool]
    rows_by_display = {display: current_by_code[api] for display, api in resolved_all}
    flash_signals, flash_stats = update_and_score(
        path=FLASH_STATE_PATH,
        resolved_pairs=resolved_all,
        current_by_code=current_by_code,
        reference_display=reference_display,
        reference_api_code=reference_api,
        now_ms=now_ms,
        config=config,
    )

    aliases = (config.get("market_data") or {}).get("asset_aliases", {})
    market_client = PublicMarketDataClient(config)
    intraday, market_stats = market_client.fetch_many(
        [display for display, _ in resolved_all],
        now_ms=now_ms,
        aliases=aliases if isinstance(aliases, Mapping) else {},
    )
    market_section = config.get("market_data") or {}
    exact_count = int(market_stats.get("exact_count", 0))
    minimum_exact = max(
        int(market_section.get("minimum_exact_assets", 18)),
        int(len(resolved_all) * float(market_section.get("minimum_pool_coverage", 0.35))),
    )
    btc_public = intraday.get(reference_display)
    if bool(market_section.get("require_btc_one_minute", True)) and (
        btc_public is None or btc_public.data_quality not in {"good", "partial"}
    ):
        raise RuntimeError("BTC-1-Minuten-Daten fehlen; aus Sicherheitsgründen kein Bericht.")
    if exact_count < minimum_exact:
        raise RuntimeError(
            f"Zu wenig echte 1-Minuten-Daten ({exact_count}/{len(resolved_all)}, benötigt {minimum_exact}); "
            "aus Sicherheitsgründen kein Bericht."
        )
    market_quality = build_market_quality(
        btc_intraday=intraday.get(reference_display),
        intraday_by_display=intraday,
        rows_by_display=rows_by_display,
        reference_display=reference_display,
    )
    categories, coin_categories = build_category_context(
        config=config,
        flash_signals=flash_signals,
        intraday_by_display=intraday,
    )
    category_trends, category_state_stats = update_category_state(
        path=CATEGORY_STATE_PATH,
        assessments=categories,
        now_ms=now_ms,
        config=config,
    )

    raw_volume_7d: dict[str, float | None] = {}
    for display, api_code in resolved_all:
        raw_volume = current_by_code[api_code].get("volume")
        raw_volume_7d[display] = volume_trend_from_context(
            daily_state,
            display,
            current_volume=None if raw_volume in (None, "") else float(raw_volume),
            now_ms=now_ms,
            days=7,
        )
    volume_context = seven_day_volume_context(raw_volume_7d)
    cap_raw = small_cap_bonuses(rows_by_display, minimum_reliable_volume=float(config.get("minimum_reliable_volume_usd", 500_000)))
    cap_scale = float((config.get("ranking_weights") or {}).get("small_market_cap_bonus_cap", 4.0)) / 10.0
    cap_bonuses = {display: value * cap_scale for display, value in cap_raw.items()}
    btc_context = {
        display: btc_performance_context(current_by_code[api], current_by_code[reference_api], is_reference=(display == reference_display))
        for display, api in resolved_all
    }
    unlocks = {display: unlock_context(display, config, now=now) for display, _ in resolved_all}

    prices = {
        display: float((intraday.get(display).latest_close if intraday.get(display) and intraday.get(display).latest_close else current_by_code[api].get("rate")) or 0.0)
        for display, api in resolved_all
    }
    candle_ranges = {
        display: {
            "open_ms": metrics.latest_candle_open_ms,
            "high": metrics.latest_high,
            "low": metrics.latest_low,
        }
        for display, metrics in intraday.items()
    }
    outcome_state, live_profiles, outcome_stats = update_and_resolve(
        path=OUTCOME_STATE_PATH,
        prices=prices,
        candle_ranges=candle_ranges,
        now_ms=now_ms,
        config=config,
    )

    btc_metrics = intraday.get(reference_display)
    short_by_display = {
        display: _short_from_sources(display, intraday.get(display), flash_signals.get(display), btc_metrics)
        for display, _ in resolved_all
    }
    preliminary = {
        display: _assessment_for(
            display=display,
            api_code=api,
            current_by_code=current_by_code,
            short_by_display=short_by_display,
            flash_signals=flash_signals,
            intraday=intraday,
            reference_display=reference_display,
            market_quality=market_quality,
            daily_state=daily_state,
            live_profiles=live_profiles,
            unlocks=unlocks,
            coin_categories=coin_categories,
            category_trends=category_trends,
            config=config,
        )
        for display, api in resolved_pool
    }
    execution_candidates = sorted(
        preliminary,
        key=lambda display: max(float(preliminary[display].get("entry_score", 0.0)), float(preliminary[display].get("exit_score", 0.0))),
        reverse=True,
    )
    execution_stats = market_client.enrich_top_candidates(
        intraday,
        execution_candidates,
        max_count=int((config.get("market_data") or {}).get("top_execution_checks", 12)),
    )
    assessments = {
        display: _assessment_for(
            display=display,
            api_code=api,
            current_by_code=current_by_code,
            short_by_display=short_by_display,
            flash_signals=flash_signals,
            intraday=intraday,
            reference_display=reference_display,
            market_quality=market_quality,
            daily_state=daily_state,
            live_profiles=live_profiles,
            unlocks=unlocks,
            coin_categories=coin_categories,
            category_trends=category_trends,
            config=config,
        )
        for display, api in resolved_pool
    }
    signal_states, signal_stats = update_signal_states(
        path=SIGNAL_STATE_PATH,
        assessments=assessments,
        now_ms=now_ms,
        config=config,
    )

    common = {
        "now": now,
        "timezone": str(config.get("timezone", "Europe/Berlin")),
        "block_hours": int(config.get("time_block_hours", 4)),
        "min_samples": int(config.get("seasonality_min_samples", 8)),
        "minimum_observations": int(config.get("seasonality_min_observations", 56)),
        "config": config,
    }
    analyses: list[CoinAnalysis] = []
    record_candidates: list[dict[str, Any]] = []
    for display, api_code in resolved_pool:
        seasonality, week_returns = context_for_coin(daily_state, display)
        vol = volume_context.get(display) or {}
        btc24, btc24_color, btc7, btc7_color = btc_context[display]
        flash = flash_signals.get(display)
        unlock = unlocks.get(display) or {}
        item = build_coin_analysis(
            display_code=display,
            api_code=api_code,
            current=current_by_code[api_code],
            short=short_by_display[display],
            history=[],
            is_reference=False,
            seasonality_override=seasonality,
            week_samples_override=week_returns,
            map_flash_score=float(flash.score if flash else 0.0),
            map_flash_direction=str(flash.direction if flash else "="),
            map_volatility_score=float(flash.volatility_score if flash else 0.0),
            map_recovery_score=float(flash.recovery_score if flash else 0.0),
            map_recovery_color=str(flash.recovery_color if flash else YELLOW),
            volume_7d_pct=vol.get("pct"),
            volume_7d_color=str(vol.get("color") or WHITE),
            volume_7d_bonus=float(vol.get("bonus") or 0.0),
            btc_24h_pct=float(btc24),
            btc_24h_color=btc24_color,
            btc_7d_pct=float(btc7),
            btc_7d_color=btc7_color,
            market_cap_bonus=float(cap_bonuses.get(display, 0.0)),
            unlock_penalty=float(unlock.get("penalty") or 0.0),
            unlock_risk=str(unlock.get("risk") or "none"),
            unlock_event_date=unlock.get("event_date"),
            **common,
        )
        assessment = dict(assessments[display])
        state = signal_states[display]
        assessment.update({
            "entry_score": state.entry_score,
            "exit_score": state.exit_score,
            "ranking_score": state.ranking_score,
            "direction": state.direction,
            "color": state.color,
            "strength_count": state.strength_count,
            "qualified_entry": state.qualified_entry,
            "qualified_exit": state.qualified_exit,
        })
        apply_opportunity_analysis(item, assessment, market_quality=market_quality.to_dict())
        item.signal_state = state.state
        item.score_velocity = state.score_velocity
        analyses.append(item)
        record_candidates.append({"display": display, **assessment})

    record_stats = record_entry_candidates(
        path=OUTCOME_STATE_PATH,
        state=outcome_state,
        candidates=record_candidates,
        prices=prices,
        now_ms=now_ms,
        config=config,
    )

    buy_funnel = {
        "safe_candidates": sum(bool(value.get("buy_candidate_ready", False)) for value in assessments.values()),
        "qualified_buys": sum(bool(value.get("qualified_entry", False)) for value in assessments.values()),
        "qualified_sells": sum(bool(value.get("qualified_exit", False)) for value in assessments.values()),
    }
    print(
        "Signal-Funnel: "
        f"{buy_funnel['safe_candidates']} sichere Kaufkandidaten, "
        f"{buy_funnel['qualified_buys']} qualifizierte Käufe, "
        f"{buy_funnel['qualified_sells']} qualifizierte Verkäufe.",
        flush=True,
    )
    if buy_funnel["safe_candidates"] == 0:
        blocked = sorted(
            assessments.items(),
            key=lambda pair: float(pair[1].get("entry_score", 0.0)),
            reverse=True,
        )[:5]
        for code, value in blocked:
            print(
                f"Kaufcheck {code}: E={float(value.get('entry_score', 0.0)):.1f} "
                f"K={float(value.get('category_score', 0.0)):.1f} "
                f"günstig={float(value.get('cheap_price_score', 0.0)):.1f} "
                f"stabil={float(value.get('stabilization_score', 0.0)):.1f} "
                f"Nachfrage={float(value.get('demand_score', 0.0)):.1f} "
                f"Gründe={'; '.join(value.get('reasons', ())[-3:])}",
                flush=True,
            )

    top = select_category_entries(
        analyses,
        assessments,
        categories,
        signal_states=signal_states,
        state_stats=signal_stats,
        top_count=int(config.get("top_coin_count", 8)),
        config=config,
    )
    if not top:
        raise RuntimeError("Keine auswertbaren Kauf-/Verkaufsseite im Pool; Bericht wird nicht gesendet.")
    category_line = format_category_line(
        categories,
        config=config,
        btc_color=market_quality.btc_reference_color,
        generated_at=now,
        timezone=str(config.get("timezone", "Europe/Berlin")),
    )
    report = build_report(category_line, top, generated_at=now, timezone=str(config.get("timezone", "Europe/Berlin")))

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    (output / "latest_report.txt").write_text(report + "\n", encoding="utf-8")
    (output / "latest_analysis.json").write_text(json.dumps({
        "version": APP_VERSION,
        "generated_at": now.isoformat(),
        "category_line": category_line,
        "top_coins": [analysis_to_dict(item) for item in top],
        "categories": {code: item.to_dict() for code, item in categories.items()},
        "category_trends": {code: item.to_dict() for code, item in category_trends.items()},
        "market_quality": market_quality.to_dict(),
        "signal_funnel": buy_funnel,
        "market_data": {"stats": market_stats, "execution": execution_stats, "coins": {k: v.to_dict() for k, v in intraday.items()}},
        "signal_state": {"version": SIGNAL_STATE_VERSION, "stats": signal_stats, "coins": {k: v.to_dict() for k, v in signal_states.items()}},
        "flash": {"version": FLASH_STATE_VERSION, "stats": flash_stats},
        "category_state": {"version": CATEGORY_STATE_VERSION, "stats": category_state_stats},
        "outcomes": {"version": OUTCOME_STATE_VERSION, "update": outcome_stats, "record": record_stats},
        "unresolved": unresolved,
        "lcw_fast_requests": 1,
        "public_requests": market_client.request_count,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n" + report + "\n")
    if should_send:
        send_now, reason, digest = report_send_decision(
            path=NOTIFICATION_STATE_PATH,
            report=report,
            now_ms=now_ms,
            config=config,
            force=force_discord,
        )
        if send_now:
            send_discord(
                webhook_url=webhook_url,
                content=report,
                username=str(config.get("discord_username", "CF v3.5.0")),
                avatar_url=str(config.get("discord_avatar_url", "")).strip(),
                timeout=int(config.get("request_timeout_seconds", 25)),
            )
            mark_report_sent(path=NOTIFICATION_STATE_PATH, digest=digest, now_ms=now_ms, reason=reason)
            print(f"Discord gesendet ({reason}).")
        else:
            print("Discord nicht gesendet: Bericht unverändert und Herzschlag noch nicht fällig.")
    else:
        print("Testmodus: keine Discord-Nachricht.")
    return 0


def run() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    api_key = os.getenv("LCW_API_KEY", "").strip()
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not api_key:
        raise ValueError("GitHub Secret LCW_API_KEY fehlt.")
    should_send = env_bool("SEND_DISCORD", True) and not args.no_send and not args.longterm_only
    if should_send and not webhook:
        raise ValueError("GitHub Secret DISCORD_WEBHOOK_URL fehlt.")
    force_longterm = args.force_longterm or env_bool("FORCE_LONGTERM", False)
    _, reusable_map = prepare_longterm_context(config, api_key, force=force_longterm)
    if args.longterm_only:
        return 0
    return run_monitor(
        config,
        api_key,
        webhook,
        should_send=should_send,
        force_discord=args.force_discord or env_bool("FORCE_DISCORD", False),
        reused_map=reusable_map,
    )


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
# Package revision: v3.5.0-buy-selection-consistency-r6
