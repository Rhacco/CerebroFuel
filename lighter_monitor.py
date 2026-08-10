# r2
"""Lighter-native signal engine with J/E context for CF v7.0.0."""
from __future__ import annotations

import json
import math
import re
import statistics
import time
import threading
import unicodedata
from collections import deque
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from regime_context import calculate_regimes
from extremity_context import calculate_extremity, extremity_color
from swing_context import calculate_pin, calculate_swing_metrics
from springer_context import calculate_springer_strength
from incident_context import IncidentSnapshot, detect_spontaneous_incidents
from signal_streak_state import apply_signal_streaks
from signal_transition_guard import apply_signal_transition_guard
from signal_evaluator import update_signal_evaluation

APP_VERSION = "7.0.0"
PACKAGE_REVISION = "r2"
ANALYSIS_WINDOWS = (5, 10, 15, 20, 60)
DISPLAY_WINDOWS = (5, 20, 60)
GLOBAL_BTC_EVENT_KINDS = {
    "FOMC", "BEIGE", "CPI", "NFP", "PPI", "JOLTS", "ECI",
    "PRODUCTIVITY", "IMPORT_PRICES", "GDP", "PCE", "TRADE",
    "RETAIL", "DURABLE", "HOUSING_STARTS", "NEW_HOME_SALES",
    "FACTORY_ORDERS", "CONSTRUCTION", "BUSINESS_INVENTORIES",
    "ADVANCE_INDICATORS", "CLAIMS", "ADP",
    "CONSUMER_CONFIDENCE", "MICHIGAN", "ISM_MANUFACTURING",
    "ISM_SERVICES", "EXPIRY", "ETF",
}

STATE_TIER = {
    "BUY": 4,
    "SELL": 4,
    "STRONG_LONG": 3,
    "STRONG_SHORT": 3,
    "WATCH_LONG": 2,
    "WATCH_SHORT": 2,
    "NO_TRADE": 1,
    "INVALID_DATA": 0,
}

DAILY_CACHE_SCHEMA = "daily-candles-v700-r1"
COMPATIBLE_CACHE_REVISIONS = {"r1", PACKAGE_REVISION}
FUNDING_NORMALIZATION_HOURS = 8.0


def _load_daily_candle_cache(
    path: Path | None,
    *,
    now: datetime,
    allowed: set[str],
    refresh_minutes: int,
    max_stale_hours: int,
) -> tuple[dict[str, list[Mapping[str, Any]]], dict[str, list[Mapping[str, Any]]], dict[str, str]]:
    """Return fresh rows, stale fallback rows and original per-symbol timestamps."""
    if path is None or not path.exists():
        return {}, {}, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, {}
    if (
        payload.get("schema") != DAILY_CACHE_SCHEMA
        or payload.get("app_version") != APP_VERSION
        or payload.get("package_revision") not in COMPATIBLE_CACHE_REVISIONS
    ):
        return {}, {}, {}
    fresh: dict[str, list[Mapping[str, Any]]] = {}
    fallback: dict[str, list[Mapping[str, Any]]] = {}
    timestamps: dict[str, str] = {}
    current = now.astimezone(timezone.utc)
    for raw_symbol, item in (payload.get("symbols") or {}).items():
        symbol = str(raw_symbol).upper()
        if symbol not in allowed or not isinstance(item, Mapping):
            continue
        stamp = str(item.get("updated_at") or "")
        try:
            updated = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        rows = item.get("rows")
        if not isinstance(rows, list) or len(rows) < 30:
            continue
        age_seconds = max(0.0, (current - updated).total_seconds())
        day_start_ms = int(current.timestamp() // 86_400) * 86_400_000
        latest_completed_close_ms = day_start_ms
        clean_stamps = sorted(
            _timestamp_ms(row) for row in rows
            if isinstance(row, Mapping) and _timestamp_ms(row) > 0
        )
        latest_row_ms = clean_stamps[-1] if clean_stamps else 0
        no_daily_gaps = all(
            right - left == 86_400_000
            for left, right in zip(clean_stamps, clean_stamps[1:])
        )
        has_latest_completed_day = (
            latest_row_ms == latest_completed_close_ms and no_daily_gaps
        )
        # A fallback may be stale in fetch time, but never stale in market-day
        # coverage. Missing yesterday is safer treated as unavailable than as a
        # seemingly current multi-day regime/Springer input.
        if age_seconds <= max_stale_hours * 3600 and has_latest_completed_day:
            fallback[symbol] = rows
            timestamps[symbol] = updated.isoformat()
        if age_seconds <= refresh_minutes * 60 and has_latest_completed_day:
            fresh[symbol] = rows
    return fresh, fallback, timestamps


def _write_daily_candle_cache(
    path: Path | None,
    *,
    now: datetime,
    allowed: set[str],
    rows_by_symbol: Mapping[str, list[Mapping[str, Any]]],
    refreshed_symbols: set[str],
    old_timestamps: Mapping[str, str],
) -> None:
    if path is None:
        return
    symbols: dict[str, Any] = {}
    now_iso = now.astimezone(timezone.utc).isoformat()
    for symbol in sorted(allowed):
        rows = rows_by_symbol.get(symbol)
        if not rows:
            continue
        stamp = now_iso if symbol in refreshed_symbols else old_timestamps.get(symbol)
        if not stamp:
            continue
        symbols[symbol] = {"updated_at": stamp, "rows": rows}
    payload = {
        "schema": DAILY_CACHE_SCHEMA,
        "app_version": APP_VERSION,
        "package_revision": PACKAGE_REVISION,
        "symbols": symbols,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def _discord_display_columns(value: str) -> int:
    """Conservative display-width estimate for Discord's proportional UI."""
    width = 0
    for char in str(value):
        codepoint = ord(char)
        if char == "\u200d" or 0xFE00 <= codepoint <= 0xFE0F:
            continue
        if unicodedata.combining(char):
            continue
        if unicodedata.east_asian_width(char) in {"W", "F"} or (
            0x1F000 <= codepoint <= 0x1FAFF
        ):
            width += 2
        else:
            width += 1
    return width

def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    value = int(_f(row.get("t")))
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _pct(start: float, end: float) -> float:
    return 0.0 if start <= 0 else (end / start - 1.0) * 100.0


def _mean(values: Iterable[float], default: float = 0.0) -> float:
    rows = list(values)
    return statistics.mean(rows) if rows else default


def _direction(value: float, fallback: int = 1) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 1 if fallback >= 0 else -1


def _directional(value: float, direction: int) -> float:
    return value * (1 if direction >= 0 else -1)


@dataclass
class Window:
    minutes: int
    price_pct: float | None = None
    volume_ratio: float | None = None
    score: float = 0.0
    quality: str = "invalid"
    reason: str = "missing candles"


@dataclass
class Setup:
    kind: str
    direction: int = 0
    score: float = 0.0
    phase: str = "none"
    volume_score: float = 0.0
    age_minutes: int | None = None
    exit_hint: bool = False
    confirmations: int = 0
    event_strength: float = 0.0
    event_timestamp_ms: int | None = None
    move_pct: float = 0.0
    rejection_fraction: float = 0.0
    recovery_fraction: float = 0.0
    peak_score: float = 0.0
    expected_edge_pct: float = 0.0
    structural_reclaim: bool = False
    relative_confirmed: bool = True
    relative_participation: float | None = None
    relative_opposition: bool = False
    new_extreme_after_event: bool = False
    reclaim_level: float | None = None
    invalidation_price: float | None = None
    boundary_distance_pct: float = 0.0
    approach_confirmed: bool = False
    clean_boundary_test: bool = False
    preview_only: bool = False
    reason: str = ""


@dataclass
class Signal:
    symbol: str
    alias: str
    state: str = "INVALID_DATA"
    selected_setup: str = "NONE"
    opportunity: float = 0.0
    trade_readiness: float = 0.0
    direction: float = 0.0
    confidence: float = 0.0
    data_quality: float = 0.0
    activity_score: float = 0.0
    execution_score: float = 0.0
    liquidity_score: float = 0.0
    volume_confirmation: float = 0.0
    tape_quality: float = 0.0
    volume_coverage: float = 0.0
    volume_spike_share: float = 1.0
    attention_score: float = 0.0
    btc_context: float | None = None
    cost_pct: float | None = None
    long_cost_pct: float | None = None
    short_cost_pct: float | None = None
    funding_8h_pct: float | None = None
    funding_hourly_pct: float | None = None
    volume_24h: float = 0.0
    open_interest_usd: float = 0.0
    volume_oi: float | None = None
    price: float = 0.0
    live_price: float = 0.0
    candle_timestamp_ms: int = 0
    noise_pct: float = 0.0
    min_quote_amount: float = 0.0
    platform_max_leverage: float = 0.0
    maintenance_margin_pct: float = 0.0
    taker_fee_pct: float = 0.0
    extremity_available: bool = False
    extremity_score: float = 0.0
    extremity_confidence: float = 0.0
    extremity_momentum: float = 0.0
    extremity_vwap: float = 0.0
    extremity_range: float = 0.0
    extremity_funding: float = 0.0
    extremity_regime_adjustment: float = 0.0
    extremity_intraday: float = 0.0
    extremity_swing: float = 0.0
    extremity_swing_available: bool = False
    extremity_return_1d: float | None = None
    extremity_return_3d: float | None = None
    extremity_return_7d: float | None = None
    swing_available: bool = False
    swing_speed_pct: float = 0.0
    swing_speed_bps: float = 0.0
    swing_speed_score: float = 0.0
    swing_speed_ratio: float = 0.0
    swing_turnover_5m_pct: float = 0.0
    swing_volume_pulse_ratio: float = 0.0
    live_activity_score: float = 0.0
    two_sided_score: float = 0.0
    springer_class: str = ""
    springer_available: bool = False
    springer_score: float = 0.0
    springer_reliability: float = 0.0
    springer_daily_range_pct: float = 0.0
    springer_intraday_impulse_pct: float = 0.0
    btc_pin_available: bool = False
    btc_pin_level: float = 0.0
    btc_pin_score: float = 0.0
    technical_stop_price: float | None = None
    technical_stop_pct: float | None = None
    regime_available: bool = False
    regime_score: float = 0.0
    regime_consistency: float = 0.0
    regime_modifier: float = 0.0
    return_7d: float | None = None
    return_14d: float | None = None
    return_30d: float | None = None
    relative_7d: float | None = None
    relative_14d: float | None = None
    relative_30d: float | None = None
    btc_rebound_pct: float | None = None
    rebound_participation: float | None = None
    relative_drift_60m: float | None = None
    event_code: str = ""
    event_display_code: str = ""
    event_kind: str = ""
    event_title: str = ""
    event_priority: float = 0.0
    event_risk: float = 0.0
    event_score_available: bool = True
    event_source_coverage: float = 1.0
    event_block_new: bool = False
    event_leverage_cap: int | None = None
    event_source_name: str = ""
    event_source_url: str = ""
    event_starts_at: str | None = None
    event_is_global: bool = False
    chase_warning: bool = False
    chase_blocked: bool = False
    base_trade_readiness: float = 0.0
    state_limited_by_setup: bool = False
    state_limited_by_guard: bool = False
    windows: dict[int, Window] = field(default_factory=dict)
    early: Setup = field(default_factory=lambda: Setup("EARLY"))
    trend: Setup = field(default_factory=lambda: Setup("TREND"))
    reversal: Setup = field(default_factory=lambda: Setup("REVERSAL"))
    action_streak_count: int = 0
    action_streak_action: str = ""
    action_streak_direction: int = 0
    transition_guard_active: bool = False
    transition_guard_from_direction: int = 0
    transition_guard_direction_streak: int = 0
    transition_guard_confirmed_streak: int = 0
    transition_guard_reason: str = ""
    reasons: list[str] = field(default_factory=list)


class LighterClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 15.0,
        retries: int = 3,
        closed_candle_delay_seconds: int = 8,
        request_limit_per_minute: int = 54,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, min(4, retries))
        self.closed_candle_delay_seconds = max(0, min(30, closed_candle_delay_seconds))
        # Standard Lighter accounts are limited to 60 REST calls per rolling
        # minute. Keep a small reserve for retries and coordinate every worker
        # thread through one shared rolling-window gate.
        self.request_limit_per_minute = max(10, min(60, int(request_limit_per_minute)))
        self._rate_lock = threading.Lock()
        self._request_times: deque[float] = deque()
        self._blocked_until = 0.0

    def _wait_for_request_slot(self) -> None:
        while True:
            sleep_for = 0.0
            with self._rate_lock:
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] >= 60.0:
                    self._request_times.popleft()
                if now < self._blocked_until:
                    sleep_for = self._blocked_until - now
                elif len(self._request_times) >= self.request_limit_per_minute:
                    sleep_for = max(0.01, 60.0 - (now - self._request_times[0]) + 0.01)
                else:
                    self._request_times.append(now)
                    return
            time.sleep(min(max(sleep_for, 0.01), 60.0))

    def _apply_rate_limit_cooldown(self, exc: HTTPError) -> None:
        delay = 60.0
        raw = ""
        try:
            raw = str(exc.headers.get("Retry-After", "")).strip()
        except Exception:
            raw = ""
        if raw:
            try:
                delay = max(0.5, min(120.0, float(raw)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = max(0.5, min(120.0, retry_at.timestamp() - time.time()))
                except Exception:
                    delay = 60.0
        with self._rate_lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + delay)

    def get(self, path: str, **params: Any) -> Mapping[str, Any]:
        url = self.base + path
        if params:
            url += "?" + urlencode(params)
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": f"cf/{APP_VERSION}"},
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._wait_for_request_slot()
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, Mapping) or int(payload.get("code", 200)) != 200:
                    raise RuntimeError(f"Lighter-Antwort ungültig: {path}")
                return payload
            except HTTPError as exc:
                last_error = exc
                if exc.code in {405, 429}:
                    self._apply_rate_limit_cooldown(exc)
                elif exc.code not in {408, 425, 500, 502, 503, 504}:
                    raise
            except (URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
            if attempt + 1 < self.retries:
                # A shared 429/405 cooldown is already enforced by the gate.
                # Other transient failures use a short bounded backoff.
                if not isinstance(last_error, HTTPError) or last_error.code not in {405, 429}:
                    time.sleep(0.45 * (2 ** attempt))
        raise RuntimeError(f"Lighter-Aufruf fehlgeschlagen: {path}: {last_error}")

    def markets(self) -> list[Mapping[str, Any]]:
        return list(self.get("/orderBookDetails", filter="perp").get("order_book_details") or [])

    def funding(self) -> list[Mapping[str, Any]]:
        return list(self.get("/funding-rates").get("funding_rates") or [])

    def candles(self, market_id: int, count: int = 360) -> list[Mapping[str, Any]]:
        now = int(time.time())
        payload = self.get(
            "/candles",
            market_id=market_id,
            resolution="1m",
            start_timestamp=now - (count + 15) * 60,
            end_timestamp=now,
            count_back=min(500, max(130, count)),
            set_timestamp_to_end="true",
        )
        rows_by_time: dict[int, Mapping[str, Any]] = {}
        closed_end_ms = (
            (now - self.closed_candle_delay_seconds) // 60
        ) * 60_000
        for row in list(payload.get("c") or []):
            stamp = _timestamp_ms(row)
            if stamp > 0 and stamp <= closed_end_ms:
                rows_by_time[stamp] = row
        rows = [rows_by_time[key] for key in sorted(rows_by_time)]
        if rows and _timestamp_ms(rows[-1]) < closed_end_ms - 120_000:
            raise RuntimeError("Kerzendaten sind veraltet")
        return rows[-count:]

    def daily_candles(self, market_id: int, count: int = 40) -> list[Mapping[str, Any]]:
        now = int(time.time())
        # Lighter documents candle `t` as the bucket start when
        # set_timestamp_to_end=false.  For regime/extremity every stored daily
        # close is therefore normalised to its bucket END timestamp.  This makes
        # it impossible for a close that occurred after a historical target to
        # be selected as if it had already been known at the bucket start.
        payload = self.get(
            "/candles",
            market_id=market_id,
            resolution="1d",
            start_timestamp=now - (count + 7) * 86_400,
            end_timestamp=now,
            count_back=min(500, max(36, count + 2)),
            set_timestamp_to_end="false",
        )
        day_start_ms = (now // 86_400) * 86_400_000
        rows_by_time: dict[int, Mapping[str, Any]] = {}
        for raw in list(payload.get("c") or []):
            start_ms = _timestamp_ms(raw)
            if 0 < start_ms < day_start_ms and _f(raw.get("c")) > 0:
                close_ms = start_ms + 86_400_000
                row = dict(raw)
                row["t"] = close_ms
                rows_by_time[close_ms] = row
        rows = [rows_by_time[key] for key in sorted(rows_by_time)]
        latest_expected_ms = day_start_ms
        if rows and _timestamp_ms(rows[-1]) != latest_expected_ms:
            raise RuntimeError("letzte abgeschlossene Tageskerze fehlt")
        stamps = [_timestamp_ms(row) for row in rows]
        if any(right - left != 86_400_000 for left, right in zip(stamps, stamps[1:])):
            raise RuntimeError("Tageskerzen enthalten eine Datenlücke")
        return rows[-count:]

    def book(self, market_id: int, limit: int = 50) -> Mapping[str, Any]:
        return self.get("/orderBookOrders", market_id=market_id, limit=limit)


def _contiguous(rows: list[Mapping[str, Any]]) -> bool:
    return all(
        _timestamp_ms(current) - _timestamp_ms(previous) == 60_000
        for previous, current in zip(rows, rows[1:])
    )


def _window(candles: list[Mapping[str, Any]], minutes: int) -> Window:
    needed = minutes * 2
    if len(candles) < needed:
        return Window(minutes=minutes, reason=f"{len(candles)}/{needed} candles")
    rows = candles[-needed:]
    if not _contiguous(rows):
        return Window(minutes=minutes, reason="candle gap")
    recent, previous = rows[-minutes:], rows[:-minutes]
    try:
        prices = [
            _f(row[key])
            for row in rows
            for key in ("o", "h", "l", "c")
        ]
        start, end = _f(recent[0]["o"]), _f(recent[-1]["c"])
    except (KeyError, TypeError):
        return Window(minutes=minutes, reason="malformed candle")
    recent_volume = sum(_f(row.get("V")) for row in recent)
    previous_volume = sum(_f(row.get("V")) for row in previous)
    if min(prices, default=0.0) <= 0 or start <= 0 or min(recent_volume, previous_volume) <= 0:
        return Window(minutes=minutes, reason="price/quote-volume")

    price = _pct(start, end)
    ratio = recent_volume / previous_volume
    returns = [
        abs(_pct(_f(left.get("c")), _f(right.get("c"))))
        for left, right in zip(rows, rows[1:])
        if _f(left.get("c")) > 0 and _f(right.get("c")) > 0
    ]
    noise = max(0.015, statistics.median(returns) * 1.4826 if returns else 0.015)
    expected_move = max(0.06, noise * math.sqrt(minutes))
    price_units = _clamp(price / expected_move, -3.0, 3.0)
    flat_tolerance = max(0.04, expected_move * 0.32)

    # Price determines direction. Quote volume is deliberately only a
    # bounded strength modifier because candle volume has no buyer/seller sign.
    # A large volume spike with flat price must therefore never become a Long
    # or Short signal by itself.
    volume_factor = _clamp(
        1.0 + math.log(max(ratio, 1e-9)) * 0.22,
        0.72,
        1.28,
    )
    flat_factor = _clamp(abs(price) / flat_tolerance, 0.0, 1.0)
    score = price_units * 30.0 * volume_factor * flat_factor
    return Window(minutes, price, ratio, _clamp(score, -100.0, 100.0), "ok", "")



def _levels(book: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    result = []
    for row in book.get(side) or []:
        price = _f(row.get("price"))
        size = _f(row.get("remaining_base_amount", row.get("size")))
        if price > 0 and size > 0:
            result.append((price, size))
    result.sort(key=lambda level: level[0], reverse=side == "bids")
    return result


def _entry_fill_for_quote(
    book: Mapping[str, Any],
    quote: float,
    direction: int,
) -> tuple[float, float] | None:
    if quote <= 0 or direction not in {-1, 1}:
        return None
    side = "asks" if direction > 0 else "bids"
    remaining_quote = quote
    base = 0.0
    for price, size in _levels(book, side):
        take_quote = min(remaining_quote, price * size)
        base += take_quote / price
        remaining_quote -= take_quote
        if remaining_quote <= 1e-9:
            break
    if remaining_quote > 1e-7 or base <= 0:
        return None
    return quote / base, base


def _exit_fill_for_base(
    book: Mapping[str, Any],
    base: float,
    direction: int,
) -> float | None:
    if base <= 0 or direction not in {-1, 1}:
        return None
    side = "bids" if direction > 0 else "asks"
    remaining_base = base
    quote = 0.0
    for price, size in _levels(book, side):
        take_base = min(remaining_base, size)
        quote += take_base * price
        remaining_base -= take_base
        if remaining_base <= 1e-12:
            break
    if remaining_base > 1e-9:
        return None
    return quote / base


def _roundtrip_cost(
    book: Mapping[str, Any],
    quote: float,
    direction: int,
) -> float | None:
    """Direction-aware spread/slippage roundtrip for one quote-sized trade.

    Long enters through asks and exits through bids. Short enters through bids
    and must be buyable back through asks. Missing depth on either required
    side is therefore a hard `None`, never an apparently cheap opposite-side
    roundtrip.
    """
    entry = _entry_fill_for_quote(book, quote, direction)
    if entry is None:
        return None
    entry_price, base = entry
    exit_price = _exit_fill_for_base(book, base, direction)
    if exit_price is None or entry_price <= 0:
        return None
    cost = (
        (entry_price - exit_price) / entry_price * 100.0
        if direction > 0
        else (exit_price - entry_price) / entry_price * 100.0
    )
    return max(0.0, cost)


def _series(candles: list[Mapping[str, Any]], count: int) -> list[Mapping[str, Any]]:
    rows = candles[-count:]
    if len(rows) < count or not _contiguous(rows):
        return []
    for row in rows:
        if min(_f(row.get(key)) for key in ("o", "h", "l", "c")) <= 0:
            return []
    return rows


def _robust_noise(candles: list[Mapping[str, Any]]) -> float:
    rows = candles[-190:-10] if len(candles) >= 200 else candles[:-10]
    # Never reinterpret a multi-minute data gap as one one-minute return. The
    # current signal windows already require contiguity; the volatility
    # baseline follows the same time semantics.
    returns = [
        abs(_pct(_f(left.get("c")), _f(right.get("c"))))
        for left, right in zip(rows, rows[1:])
        if _f(left.get("c")) > 0
        and _f(right.get("c")) > 0
        and _timestamp_ms(right) - _timestamp_ms(left) == 60_000
    ]
    return max(0.015, statistics.median(returns) * 1.4826 if returns else 0.015)


def _linear_trend(closes: list[float]) -> tuple[float, float]:
    if len(closes) < 3 or min(closes) <= 0:
        return 0.0, 0.0
    values = [math.log(value) for value in closes]
    count = len(values)
    mean_x = (count - 1) / 2.0
    mean_y = statistics.mean(values)
    variance_x = sum((index - mean_x) ** 2 for index in range(count))
    if variance_x <= 0:
        return 0.0, 0.0
    slope = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(values)
    ) / variance_x
    fitted = [mean_y + slope * (index - mean_x) for index in range(count)]
    total = sum((value - mean_y) ** 2 for value in values)
    residual = sum((value - fit) ** 2 for value, fit in zip(values, fitted))
    r_squared = 0.0 if total <= 1e-15 else _clamp(1.0 - residual / total, 0.0, 1.0)
    return (math.exp(slope) - 1.0) * 100.0, r_squared


def _last_returns(rows: list[Mapping[str, Any]], count: int = 3) -> list[float]:
    closes = [_f(row.get("c")) for row in rows[-(count + 1):]]
    return [_pct(left, right) for left, right in zip(closes, closes[1:])]


def _baseline_volume(candles: list[Mapping[str, Any]], exclude: int = 12) -> float:
    rows = candles[-132:-exclude] if len(candles) >= 132 else candles[:-exclude]
    values = [_f(row.get("V")) for row in rows if _f(row.get("V")) > 0]
    return statistics.median(values) if values else 0.0



def _tape_quality(candles: list[Mapping[str, Any]], count: int = 90) -> tuple[float, float, float]:
    """Measure whether one-minute quote volume is continuous rather than gap/spike driven."""
    rows = candles[-count:]
    if len(rows) < min(60, count) or not _contiguous(rows):
        return 0.0, 0.0, 1.0
    volumes = [_f(row.get("V")) for row in rows]
    positive = [value for value in volumes if value > 0]
    coverage = len(positive) / len(volumes)
    if not positive:
        return 0.0, coverage, 1.0
    baseline = statistics.median(positive)
    recent = volumes[-20:]
    recent_total = sum(recent)
    spike_share = max(recent, default=0.0) / recent_total if recent_total > 0 else 1.0
    quiet_fraction = sum(
        value <= 0 or value < baseline * 0.035
        for value in volumes
    ) / len(volumes)
    extreme_ratio = max(recent, default=0.0) / max(baseline, 1e-9)
    coverage_score = coverage * 52.0
    concentration_score = (1.0 - _clamp((spike_share - 0.14) / 0.56, 0.0, 1.0)) * 25.0
    continuity_score = (1.0 - _clamp(quiet_fraction / 0.36, 0.0, 1.0)) * 18.0
    extreme_penalty = _clamp((extreme_ratio - 22.0) / 38.0, 0.0, 1.0) * 10.0
    return _clamp(coverage_score + concentration_score + continuity_score - extreme_penalty), coverage, spike_share


def _assess_early(
    candles: list[Mapping[str, Any]],
    noise: float,
    *,
    current_price: float | None,
    max_age_minutes: int,
    max_consumed_fraction: float,
    minimum_efficiency: float,
    minimum_volume_consistency: int,
    approach_noise_multiple: float,
    probe_noise_multiple: float,
    probe_max_consumed_fraction: float,
) -> Setup:
    """Find a fresh expansion from compression before most of the expected move is consumed."""
    rows = _series(candles, 90)
    if not rows:
        return Setup("EARLY", reason="insufficient contiguous history")
    baseline_volume = _baseline_volume(candles, exclude=8)
    if baseline_volume <= 0:
        return Setup("EARLY", reason="insufficient quote-volume baseline")

    pre = rows[-42:-7]
    trigger = rows[-7:]
    older = rows[-82:-42]
    pre_high = max(_f(row.get("h")) for row in pre)
    pre_low = min(_f(row.get("l")) for row in pre)
    older_high = max(_f(row.get("h")) for row in older)
    older_low = min(_f(row.get("l")) for row in older)
    pre_range = max(0.0, _pct(pre_low, pre_high))
    older_range = max(pre_range, _pct(older_low, older_high))
    pre_returns = _last_returns(pre, min(20, len(pre) - 1))
    older_returns = _last_returns(older, min(25, len(older) - 1))
    pre_noise = statistics.median(abs(value) for value in pre_returns) if pre_returns else noise
    older_noise = statistics.median(abs(value) for value in older_returns) if older_returns else noise
    range_ratio = pre_range / max(older_range, noise, 1e-9)
    noise_ratio = pre_noise / max(older_noise, noise * 0.35, 1e-9)
    compression_score = _clamp(
        52.0
        + (1.0 - _clamp(range_ratio, 0.0, 1.4)) * 30.0
        + (1.0 - _clamp(noise_ratio, 0.0, 1.5)) * 24.0
    )

    closes = [_f(row.get("c")) for row in trigger]
    volumes = [_f(row.get("V")) for row in trigger]
    returns = [_pct(left, right) for left, right in zip(closes, closes[1:])]
    target_move = max(0.16, pre_range * 0.82, noise * math.sqrt(20.0) * 3.6)
    live_price = _f(current_price) if current_price is not None else closes[-1]
    live_gap = abs(_pct(closes[-1], live_price)) if live_price > 0 else 999.0
    if live_gap > max(0.60, target_move * 2.0):
        live_price = closes[-1]
    candidates: list[Setup] = []
    for direction in (1, -1):
        boundary = pre_high if direction > 0 else pre_low
        directional_returns = [_directional(value, direction) for value in returns]
        move_3 = sum(directional_returns[-3:])
        move_6 = sum(directional_returns)
        first_3 = sum(directional_returns[:3])
        acceleration = move_3 - first_3
        path = sum(abs(value) for value in returns)
        efficiency = max(0.0, move_6) / max(path, 1e-9)
        extension = _directional(_pct(boundary, closes[-1]), direction)
        live_extension = _directional(_pct(boundary, live_price), direction)
        breakout_floor = max(0.022, noise * 0.42)
        approach_band = max(0.075, noise * approach_noise_multiple)
        probe_band = max(0.020, noise * probe_noise_multiple)
        crossings: list[int] = []
        for index, close in enumerate(closes):
            current = _directional(_pct(boundary, close), direction)
            previous = (
                _directional(_pct(boundary, closes[index - 1]), direction)
                if index > 0 else -999.0
            )
            if current >= breakout_floor and previous < breakout_floor:
                crossings.append(index)
        event_index = crossings[-1] if crossings else None
        age = None if event_index is None else len(closes) - 1 - event_index
        consumed = max(0.0, extension) / max(target_move, 1e-9)
        expected_edge = max(0.0, target_move - max(0.0, extension))

        ratios = [value / baseline_volume for value in volumes[-4:]]
        consistency = sum(value >= 1.05 for value in ratios)
        recent_ratio = _mean(ratios)
        recent_total = sum(volumes[-5:])
        spike_share = max(volumes[-5:], default=0.0) / recent_total if recent_total > 0 else 1.0
        volume_score = _clamp(
            38.0
            + (recent_ratio - 1.0) * 36.0
            + consistency * 10.0
            - max(0.0, spike_share - 0.58) * 90.0
        )
        momentum_floor = max(0.035, noise * 1.15)
        momentum_score = _clamp(
            42.0
            + (move_3 / max(momentum_floor, 1e-9) - 1.0) * 24.0
            + (move_6 / max(momentum_floor * 1.55, 1e-9) - 1.0) * 18.0
            + acceleration / max(noise, 1e-9) * 5.0
        )
        efficiency_score = _clamp(
            45.0 + (efficiency - minimum_efficiency) * 100.0
        )
        recency_score = 58.0 if age is None else {
            0: 100.0, 1: 94.0, 2: 84.0, 3: 62.0, 4: 38.0
        }.get(age, 15.0)
        room_score = _clamp((1.0 - consumed / max(max_consumed_fraction, 1e-9)) * 100.0)
        score = _clamp(
            compression_score * 0.18
            + momentum_score * 0.27
            + volume_score * 0.20
            + efficiency_score * 0.17
            + recency_score * 0.10
            + room_score * 0.08
        )
        positive_candles = sum(value > 0 for value in directional_returns[-4:])
        closed_near = -approach_band <= extension < breakout_floor
        live_near = -approach_band <= live_extension < breakout_floor
        approach_progress = (
            move_3 >= momentum_floor * 0.28
            and efficiency >= minimum_efficiency * 0.55
            and recent_ratio >= 0.92
            and consistency >= 1
            and positive_candles >= 2
            and compression_score >= 48.0
            and acceleration >= -noise * 0.45
        )
        approach_confirmed = bool((closed_near or live_near) and approach_progress)
        clean_test = (
            (age is None or age <= 1)
            and extension >= -probe_band
            and extension <= max(breakout_floor * 2.2, target_move * probe_max_consumed_fraction)
            and consumed <= probe_max_consumed_fraction
            and move_3 >= momentum_floor * 0.55
            and move_6 >= momentum_floor * 0.90
            and efficiency >= minimum_efficiency * 0.72
            and recent_ratio >= 1.02
            and consistency >= max(2, minimum_volume_consistency - 1)
            and positive_candles >= 3
            and compression_score >= 50.0
            and spike_share <= 0.62
            and expected_edge >= target_move * 0.45
        )
        late = (
            extension > 0
            and (
                (age is not None and age > max_age_minutes)
                or consumed > max_consumed_fraction
                or efficiency < minimum_efficiency * 0.72
                or move_3 <= 0
            )
        )
        ready = (
            event_index is not None
            and age is not None
            and age <= max_age_minutes
            and consumed <= max_consumed_fraction
            and efficiency >= minimum_efficiency
            and consistency >= minimum_volume_consistency
            and recent_ratio >= 1.12
            and move_3 >= momentum_floor
            and move_6 >= momentum_floor * 1.45
            and acceleration >= 0.0
            and compression_score >= 50.0
            and positive_candles >= 3
            and spike_share <= 0.62
        )
        forming = (
            event_index is None
            and extension < breakout_floor
            and live_extension < breakout_floor
            and approach_confirmed
            and score >= 52.0
            and expected_edge >= target_move * 0.38
        )
        if late:
            phase, exit_hint, score = "late", True, min(score, 39.0)
        elif ready:
            phase, exit_hint = "ready", False
        elif clean_test:
            phase, exit_hint, score = "strong", False, min(max(score, 64.0), 80.0)
        elif forming:
            phase, exit_hint, score = "forming", False, min(max(score, 55.0), 72.0)
        else:
            phase, exit_hint, score = "none", False, min(score, 48.0)
        candidates.append(Setup(
            "EARLY",
            direction=direction,
            score=score,
            phase=phase,
            volume_score=volume_score,
            age_minutes=age,
            exit_hint=exit_hint,
            confirmations=positive_candles,
            event_strength=momentum_score,
            event_timestamp_ms=(
                _timestamp_ms(trigger[event_index])
                if event_index is not None else None
            ),
            move_pct=max(0.0, extension),
            rejection_fraction=efficiency,
            recovery_fraction=consumed,
            peak_score=compression_score,
            expected_edge_pct=expected_edge,
            invalidation_price=boundary * (
                1.0 - direction * max(0.015, noise * 0.65) / 100.0
            ),
            boundary_distance_pct=max(0.0, -max(extension, live_extension)),
            approach_confirmed=approach_confirmed,
            clean_boundary_test=clean_test,
            preview_only=bool(phase == "forming" and live_near and not closed_near),
            reason=(
                f"compression={compression_score:.1f} move3={move_3:.3f}% "
                f"eff={efficiency:.2f} volume={recent_ratio:.2f}/{consistency} "
                f"age={age} used={consumed:.2f} edge={expected_edge:.3f}% "
                f"dist={max(0.0, -max(extension, live_extension)):.3f}% "
                f"test={int(clean_test)} preview={int(live_near and not closed_near)}"
            ),
        ))
    return max(candidates, key=lambda item: (*_setup_priority(item), item.event_strength))


def _assess_trend_once(
    candles: list[Mapping[str, Any]],
    windows: Mapping[int, Window],
    noise: float,
    config: Mapping[str, Any],
) -> Setup:
    """Detect only a quick dip/bounce inside a clear continuing trend.

    Price determines direction. Unsigned quote volume is used only to confirm
    that market activity remains elevated; it never supplies direction.
    """
    rows = _series(candles, 130)
    if not rows:
        return Setup("TREND", reason="insufficient contiguous history")
    closes = [_f(row.get("c")) for row in rows]
    volumes_all = [_f(row.get("V")) for row in rows]
    ret_60 = _pct(closes[-61], closes[-1])
    ret_15 = _pct(closes[-16], closes[-1])
    slope_60, r_squared = _linear_trend(closes[-60:])
    z_60 = ret_60 / max(noise * math.sqrt(60), 1e-9)
    z_15 = ret_15 / max(noise * math.sqrt(15), 1e-9)
    direction = _direction(z_60)
    directional_z_15 = _directional(z_15, direction)
    directional_slope = _directional(slope_60, direction) / max(noise, 1e-9)

    previous_60 = _mean(volumes_all[-120:-60])
    recent_60 = _mean(volumes_all[-60:])
    previous_20 = _mean(volumes_all[-40:-20])
    recent_20 = _mean(volumes_all[-20:])
    volume_ratio_60 = recent_60 / previous_60 if previous_60 > 0 else 0.0
    volume_ratio_20 = recent_20 / previous_20 if previous_20 > 0 else 0.0
    baseline_volume = _mean(volumes_all[-80:-20])
    recent_active = sum(
        value >= baseline_volume * 0.85
        for value in volumes_all[-6:]
    ) if baseline_volume > 0 else 0
    minimum_ratio_60 = float(config.get("trend_min_volume_ratio_60", 1.08))
    minimum_ratio_20 = float(config.get("trend_min_volume_ratio_20", 1.03))
    activity_trend = (
        volume_ratio_60 >= minimum_ratio_60
        and volume_ratio_20 >= minimum_ratio_20
        and recent_active >= 4
    )

    aligned = directional_z_15 >= 0.25
    context_strength = _clamp(
        abs(z_60) * 23.0
        + max(0.0, directional_z_15) * 16.0
        - max(0.0, -directional_z_15) * 26.0
        + r_squared * 32.0
        + (10.0 if aligned else -18.0)
        + min(10.0, max(0.0, directional_slope) * 7.0)
        - min(12.0, max(0.0, -directional_slope) * 10.0)
        + _clamp((volume_ratio_60 - 1.0) * 55.0, -12.0, 12.0)
        + _clamp((volume_ratio_20 - 1.0) * 45.0, -10.0, 10.0)
    )
    if abs(z_60) < 0.82 or context_strength < 48 or not activity_trend:
        return Setup(
            "TREND",
            direction=direction,
            score=min(48.0, context_strength * 0.58),
            phase="none",
            volume_score=_clamp(50.0 + (volume_ratio_60 - 1.0) * 70.0),
            reason=(
                f"no continuing price/activity trend v60={volume_ratio_60:.2f} "
                f"v20={volume_ratio_20:.2f} active={recent_active}/6"
            ),
        )

    lookback = rows[-10:]
    local_closes = [_f(row.get("c")) for row in lookback]
    volumes = [_f(row.get("V")) for row in lookback]
    last_close = local_closes[-1]
    max_duration = max(1, int(config.get("trend_max_pullback_duration_minutes", 3)))
    candidates: list[tuple[float, int, int, float]] = []
    # A normal candle wick is not a dip. T requires a real counter-trend close
    # and at least one completed resume candle after the local extreme.
    local_returns = _last_returns(lookback, len(lookback) - 1)
    for event_index in range(2, len(lookback) - 1):
        before_positions = list(range(max(0, event_index - max_duration), event_index))
        if not before_positions:
            continue
        if direction > 0:
            anchor_index = max(before_positions, key=lambda pos: local_closes[pos])
            anchor, extreme = local_closes[anchor_index], local_closes[event_index]
            move = -_pct(anchor, extreme)
            counter_move = local_returns[event_index - 1] < 0
        else:
            anchor_index = min(before_positions, key=lambda pos: local_closes[pos])
            anchor, extreme = local_closes[anchor_index], local_closes[event_index]
            move = _pct(anchor, extreme)
            counter_move = local_returns[event_index - 1] > 0
        if move > 0 and counter_move:
            candidates.append((move, event_index, anchor_index, extreme))
    if not candidates:
        return Setup(
            "TREND",
            direction=direction,
            score=min(52.0, context_strength * 0.62),
            phase="none",
            volume_score=_clamp(55.0 + (volume_ratio_60 - 1.0) * 65.0),
            reason="continuing trend without quick dip",
        )

    # Prefer a recent valid dip over an older, larger move.
    pullback, event_index, anchor_index, extreme = max(
        candidates,
        key=lambda item: (item[1], item[0]),
    )
    age = len(lookback) - 1 - event_index
    duration = event_index - anchor_index
    trend_move = max(abs(ret_60), noise * math.sqrt(60))
    retracement = pullback / max(trend_move, 1e-9)
    min_pullback = max(0.05, noise * 1.05)
    max_pullback = max(0.46, noise * 4.8)
    max_age = max(0, int(config.get("trend_max_pullback_age_minutes", 2)))
    valid_pullback = (
        0 <= age <= max_age
        and 1 <= duration <= max_duration
        and min_pullback <= pullback <= max_pullback
        and 0.07 <= retracement <= 0.50
    )

    rebound = _pct(extreme, last_close) if direction > 0 else _pct(extreme, last_close) * -1.0
    rebound_fraction = rebound / max(pullback, 1e-9)
    expected_edge = max(
        max(0.0, pullback - max(0.0, rebound)),
        noise * math.sqrt(5.0) * 1.7,
    )
    recent_returns = _last_returns(lookback, 3)
    directional_returns = [_directional(value, direction) for value in recent_returns]
    confirmation_count = sum(value > 0 for value in directional_returns)
    last_confirms = bool(directional_returns and directional_returns[-1] > 0)

    pullback_slice = volumes[max(0, anchor_index + 1):event_index + 1]
    resume_slice = volumes[event_index + 1:]
    pullback_volume_ratio = _mean(pullback_slice) / baseline_volume if baseline_volume > 0 else 0.0
    resume_volume_ratio = _mean(resume_slice) / baseline_volume if baseline_volume > 0 and resume_slice else 0.0
    max_pullback_volume = float(config.get("trend_max_pullback_volume_ratio", 1.05))
    min_resume_volume = float(config.get("trend_min_resume_volume_ratio", 0.90))
    volume_structure_ok = (
        pullback_volume_ratio <= max_pullback_volume
        and resume_volume_ratio >= min_resume_volume
        and resume_volume_ratio >= pullback_volume_ratio * 0.95
    )
    volume_score = _clamp(
        45.0
        + (volume_ratio_60 - 1.0) * 80.0
        + (volume_ratio_20 - 1.0) * 65.0
        + (1.0 - pullback_volume_ratio) * 30.0
        + (resume_volume_ratio - 0.85) * 35.0
    )

    middle_scores = [
        _directional(windows[minute].score, direction)
        for minute in (10, 15, 20)
        if minute in windows and windows[minute].quality == "ok"
    ]
    middle_supporting = sum(value >= 5.0 for value in middle_scores)
    middle_opposing = sum(value <= -18.0 for value in middle_scores)
    middle_confirmed = (
        len(middle_scores) == 3
        and middle_supporting >= 2
        and middle_opposing == 0
        and _mean(middle_scores) >= 6.0
    )
    pullback_score = _clamp(100.0 - abs(retracement - 0.24) * 210.0)
    rebound_score = _clamp(100.0 - abs(rebound_fraction - 0.30) * 180.0)
    trigger_score = _clamp(
        (42.0 if last_confirms else 0.0)
        + confirmation_count * 18.0
        + (18.0 if volume_structure_ok else 0.0)
    )
    score = min(90.0, _clamp(
        context_strength * 0.36
        + pullback_score * 0.22
        + rebound_score * 0.12
        + volume_score * 0.17
        + trigger_score * 0.13
    ))

    too_deep = pullback > max_pullback or retracement > 0.62
    ready = (
        valid_pullback
        and 0.12 <= rebound_fraction <= 0.58
        and last_confirms
        and confirmation_count >= 2
        and volume_structure_ok
        and volume_score >= float(config.get("trend_minimum_volume_score", 58))
        and context_strength >= 60
        and middle_confirmed
    )
    if too_deep:
        phase, exit_hint, score = "invalidated", True, min(score, 36.0)
    elif ready:
        phase, exit_hint = "ready", False
    else:
        phase, exit_hint, score = "forming", False, min(score, 65.0)
    return Setup(
        "TREND",
        direction=direction,
        score=score,
        phase=phase,
        volume_score=volume_score,
        age_minutes=age,
        exit_hint=exit_hint,
        confirmations=confirmation_count,
        event_strength=context_strength,
        event_timestamp_ms=_timestamp_ms(lookback[event_index]),
        expected_edge_pct=expected_edge,
        invalidation_price=extreme * (
            1.0 - direction * max(0.015, noise * 0.65) / 100.0
        ),
        reason=(
            f"quick-dip={pullback:.3f}% age={age} duration={duration} "
            f"retrace={retracement:.2f} rebound={rebound_fraction:.2f} "
            f"v60={volume_ratio_60:.2f} v20={volume_ratio_20:.2f} "
            f"dipV={pullback_volume_ratio:.2f} resumeV={resume_volume_ratio:.2f}"
        ),
    )


def _assess_trend(
    candles: list[Mapping[str, Any]],
    windows: Mapping[int, Window],
    noise: float,
    config: Mapping[str, Any],
) -> Setup:
    return _assess_trend_once(candles, windows, noise, config)


def _reversal_candidate(
    rows: list[Mapping[str, Any]],
    direction: int,
    noise: float,
    baseline_volume: float,
    minimum_move_pct: float,
    config: Mapping[str, Any],
) -> Setup:
    highs = [_f(row.get("h")) for row in rows]
    lows = [_f(row.get("l")) for row in rows]
    closes = [_f(row.get("c")) for row in rows]
    volumes = [_f(row.get("V")) for row in rows]
    last_close = closes[-1]
    best: tuple[float, float, int, int, float, float] | None = None

    for event_index in range(3, len(rows)):
        before = range(max(0, event_index - 7), event_index)
        if direction > 0:
            anchor_index = max(before, key=lambda pos: highs[pos])
            anchor, extreme = highs[anchor_index], lows[event_index]
            shock = _pct(anchor, extreme) * -1.0
        else:
            anchor_index = min(before, key=lambda pos: lows[pos])
            anchor, extreme = lows[anchor_index], highs[event_index]
            shock = _pct(anchor, extreme)
        duration = max(1, event_index - anchor_index)
        shock_z = shock / max(noise * math.sqrt(duration), 1e-9)
        # The largest actual shock owns the W event. A smaller later counter-
        # candle cannot reset age or reverse the interpretation.
        if shock > 0 and (
            best is None or (shock, shock_z) > (best[0], best[1])
        ):
            best = (shock, shock_z, event_index, anchor_index, extreme, anchor)

    if best is None:
        return Setup("REVERSAL")
    shock, shock_z, event_index, anchor_index, extreme, anchor = best
    age = len(rows) - 1 - event_index
    dynamic_minimum = max(
        minimum_move_pct,
        noise * math.sqrt(max(1, event_index - anchor_index)) * 2.65,
    )
    if shock < dynamic_minimum or shock_z < 2.65:
        return Setup(
            "REVERSAL",
            direction=direction,
            score=min(42.0, _clamp(shock_z * 12.0)),
            phase="none",
            age_minutes=age,
            reason=f"shock only {shock:.3f}%/{shock_z:.2f}z",
        )

    event_close = closes[event_index]
    post_rows = rows[event_index + 1:]
    post_closes = closes[event_index + 1:]
    rebound = _pct(extreme, last_close) * (1 if direction > 0 else -1)
    rebound_fraction = rebound / max(shock, 1e-9)

    post_returns: list[float] = []
    previous = event_close
    for close in post_closes:
        post_returns.append(_directional(_pct(previous, close), direction))
        previous = close
    recent_directional = post_returns[-3:]
    confirmation_count = sum(value > max(noise * 0.08, 0.005) for value in recent_directional)
    last_confirms = bool(recent_directional and recent_directional[-1] > max(noise * 0.05, 0.003))

    # Attribute volume only to the actual shock candle. Quote volume has no
    # buyer/seller sign, so it may confirm importance but never direction.
    event_volume = volumes[event_index]
    volume_ratio = event_volume / baseline_volume if baseline_volume > 0 else 0.0
    volume_score = _clamp((volume_ratio - 0.60) * 90.0) if baseline_volume > 0 else 0.0
    event_strength = shock_z * (
        1.0 + min(2.2, math.log1p(max(volume_ratio, 0.0)) * 0.42)
    )

    shock_score = _clamp(50.0 + (shock / max(dynamic_minimum, 1e-9) - 1.0) * 50.0)
    if 0.15 <= rebound_fraction <= 0.55:
        rebound_score = _clamp(72.0 + (rebound_fraction - 0.15) * 70.0)
    elif rebound_fraction < 0.15:
        rebound_score = _clamp(rebound_fraction / 0.15 * 72.0)
    else:
        rebound_score = _clamp(120.0 - rebound_fraction * 85.0)
    confirmation_score = _clamp(
        (45.0 if last_confirms else 0.0) + confirmation_count * 20.0
    )
    recency_score = {
        0: 100.0,
        1: 100.0,
        2: 92.0,
        3: 78.0,
        4: 55.0,
        5: 30.0,
    }.get(age, 0.0)
    score = _clamp(
        shock_score * 0.30
        + rebound_score * 0.25
        + volume_score * 0.20
        + confirmation_score * 0.15
        + recency_score * 0.10
    )

    reclaim_fraction_required = float(
        config.get("reversal_structural_reclaim_fraction", 0.24)
    )
    reclaim_distance = abs(anchor - extreme) * reclaim_fraction_required
    reclaim_level = (
        extreme + reclaim_distance
        if direction > 0
        else extreme - reclaim_distance
    )
    close_reclaimed = (
        last_close >= max(event_close, reclaim_level)
        if direction > 0
        else last_close <= min(event_close, reclaim_level)
    )
    tolerance_pct = max(
        0.002,
        noise * float(config.get("reversal_new_extreme_tolerance_noise", 0.18)),
    )
    if direction > 0:
        new_extreme = any(
            _f(row.get("l")) < extreme * (1.0 - tolerance_pct / 100.0)
            for row in post_rows
        )
    else:
        new_extreme = any(
            _f(row.get("h")) > extreme * (1.0 + tolerance_pct / 100.0)
            for row in post_rows
        )
    structural_reclaim = bool(
        age >= int(config.get("reversal_min_post_event_closes", 1))
        and close_reclaimed
        and rebound_fraction >= reclaim_fraction_required
        and last_confirms
        and not new_extreme
    )

    event_rebound = _pct(extreme, event_close) * (1 if direction > 0 else -1)
    event_rejection = event_rebound / max(shock, 1e-9)
    exceptional_rejection = (
        age == 0
        and shock_z >= 3.8
        and volume_ratio >= 4.0
        and 0.30 <= rebound_fraction <= 0.84
        and event_rejection >= 0.30
    )
    followthrough = (
        1 <= age <= 3
        and last_confirms
        and confirmation_count >= 1
        and volume_ratio >= 1.6
        and event_strength >= 6.8
        and 0.12 <= rebound_fraction <= 0.88
    )
    stalled = (
        age > 0
        and len(recent_directional) >= 2
        and sum(recent_directional[-2:]) < -max(noise * 0.8, shock * 0.12)
    )
    late = age > 4 or rebound_fraction > 0.92 or stalled

    if new_extreme:
        phase, exit_hint, score = "invalidated", True, min(score, 24.0)
    elif late:
        phase, exit_hint, score = "late", True, min(score, 35.0)
    elif structural_reclaim and followthrough:
        phase, exit_hint = "ready", False
    elif exceptional_rejection or followthrough:
        # Visible as W?: a real rejection/follow-through exists, but the
        # structurally important reclaim has not yet happened.
        phase, exit_hint = "strong", False
        score = min(score, 78.0)
    else:
        phase, exit_hint, score = "forming", False, min(score, 69.0)

    return Setup(
        "REVERSAL",
        direction=direction,
        score=score,
        phase=phase,
        volume_score=volume_score,
        age_minutes=age,
        exit_hint=exit_hint,
        confirmations=confirmation_count + (1 if exceptional_rejection else 0),
        event_strength=event_strength,
        event_timestamp_ms=_timestamp_ms(rows[event_index]),
        move_pct=shock,
        rejection_fraction=event_rejection,
        recovery_fraction=rebound_fraction,
        structural_reclaim=structural_reclaim,
        relative_confirmed=True,
        new_extreme_after_event=new_extreme,
        reclaim_level=reclaim_level,
        invalidation_price=extreme * (
            1.0 - direction * max(0.002, tolerance_pct) / 100.0
        ),
        reason=(
            f"shock={shock:.3f}%/{shock_z:.2f}z "
            f"rebound={rebound_fraction:.2f} reject={event_rejection:.2f} "
            f"reclaim={int(structural_reclaim)} newExtreme={int(new_extreme)} "
            f"volume={volume_ratio:.2f}"
        ),
    )

def _assess_reversal(
    candles: list[Mapping[str, Any]],
    noise: float,
    minimum_move_pct: float,
    config: Mapping[str, Any],
) -> Setup:
    rows = _series(candles, 12)
    if not rows:
        return Setup("REVERSAL", reason="insufficient contiguous history")
    baseline_volume = _baseline_volume(candles)
    candidates = [
        _reversal_candidate(rows, 1, noise, baseline_volume, minimum_move_pct, config),
        _reversal_candidate(rows, -1, noise, baseline_volume, minimum_move_pct, config),
    ]
    # The dominant shock owns the direction for its short lifetime. This keeps
    # the counter-move from being reinterpreted as a fresh opposite shock.
    return max(
        candidates,
        key=lambda item: (
            item.move_pct,
            item.event_strength,
            -(item.event_timestamp_ms or 0),
            item.score,
        ),
    )


def _activity_score(
    volume_24h: float,
    open_interest_usd: float,
    volume_oi: float | None,
    minimum_volume: float,
    minimum_oi: float,
) -> float:
    volume_score = _clamp(50.0 + 25.0 * math.log10(max(volume_24h, 1.0) / max(minimum_volume, 1.0)))
    oi_score = _clamp(50.0 + 25.0 * math.log10(max(open_interest_usd, 1.0) / max(minimum_oi, 1.0)))
    turnover_score = (
        0.0 if volume_oi is None
        else _clamp(52.0 + 24.0 * math.log10(max(volume_oi, 1e-6)))
    )
    return _clamp(volume_score * 0.45 + oi_score * 0.25 + turnover_score * 0.30)


def _setup_priority(item: Setup) -> tuple[int, float]:
    return (
        {
            "ready": 4,
            "strong": 3,
            "forming": 2,
            "late": 1,
            "invalidated": 1,
            "none": 0,
        }.get(item.phase, 0),
        item.score,
    )


def _btc_price_code(price: float) -> str:
    """Compact BTC header price: keep exactly the last four whole-dollar digits."""
    value = _f(price)
    if value <= 0.0:
        return ""
    return f"{int(value) % 10000:04d}"


def _signed_extremity_token(signal: Signal) -> str:
    if not signal.extremity_available:
        return f"{signal.alias}?00"
    value = min(99, int(round(abs(float(signal.extremity_score)))))
    sign = "+" if float(signal.extremity_score) >= 0.0 else "-"
    return f"{signal.alias}{sign}{value:02d}"


def _springer_token(signal: Signal) -> str:
    if not signal.springer_available:
        return "J??"
    value = max(0, min(99, int(round(float(signal.springer_score)))))
    return f"J{value:02d}"


def _event_risk_token(signal: Signal, config: Mapping[str, Any]) -> str:
    if not signal.event_score_available:
        return str(config.get("event_score_unknown_code", "E??"))
    value = max(0, min(99, int(round(float(signal.event_risk)))))
    return f"E{value:02d}"


def _pin_token(signal: Signal) -> str:
    if not signal.btc_pin_available:
        return "P??"
    value = max(0, min(99, int(round(float(signal.btc_pin_score)))))
    return f"P{value:02d}"


def _timing_confirmation_score(signal: Signal) -> float:
    if not signal.swing_available:
        return 0.0
    return _clamp(
        float(signal.swing_speed_score) * 0.42
        + float(signal.live_activity_score) * 0.43
        + float(signal.two_sided_score) * 0.15
    )


def _radar_activity_score(signal: Signal, config: Mapping[str, Any]) -> float:
    if signal.state == "INVALID_DATA":
        return -1.0
    speed_w = float(config.get("radar_speed_weight", 0.38))
    activity_w = float(config.get("radar_activity_weight", 0.42))
    extremity_w = float(config.get("radar_extremity_weight", 0.20))
    total = max(1e-9, speed_w + activity_w + extremity_w)
    ext = abs(float(signal.extremity_score)) if signal.extremity_available else 0.0
    speed = float(signal.swing_speed_score) if signal.swing_available else 0.0
    activity = (
        float(signal.live_activity_score)
        if signal.swing_available
        else float(signal.activity_score)
    )
    score = _clamp((speed * speed_w + activity * activity_w + ext * extremity_w) / total)
    if float(signal.tape_quality) < float(config.get("radar_min_tape_quality", 35.0)):
        score *= 0.75
    if float(signal.platform_max_leverage) < float(config.get("radar_min_platform_leverage", 5.0)):
        score *= 0.50
    return _clamp(score)


def _detail_head(signal: Signal) -> str:
    """Show current multi-window pressure; purple is reserved for NOW."""
    if signal.state == "INVALID_DATA":
        return "⚫?"
    if signal.state in {"BUY", "SELL"}:
        return "🟣" + ("▲" if signal.direction >= 0 else "▼")

    pressure = _market_bias(signal)
    if pressure is None:
        pressure = float(signal.direction)
    if abs(pressure) < 8.0:
        return "🟡 ▷  "
    if pressure >= 22.0:
        return "🟢▲"
    if pressure > 0.0:
        return "🔵▲"
    if pressure <= -22.0:
        return "🔴▼"
    return "🟠▼"


def _setup_code(signal: Signal) -> str:
    if signal.selected_setup == "REVERSAL":
        setup = signal.reversal
        return "W" if (
            setup.phase == "ready"
            and setup.structural_reclaim
            and setup.relative_confirmed
            and not setup.new_extreme_after_event
        ) else "W?"
    return {
        "EARLY": "E",
        "TREND": "T",
    }.get(signal.selected_setup, "")


def _setup_age_code(signal: Signal) -> str:
    setup = _selected_setup(signal)
    if signal.selected_setup not in {"EARLY", "TREND", "REVERSAL"} or setup.age_minutes is None:
        return ""
    return f"a{max(0, min(9, int(setup.age_minutes)))}"


def _selected_setup(signal: Signal) -> Setup:
    return {
        "EARLY": signal.early,
        "TREND": signal.trend,
        "REVERSAL": signal.reversal,
    }.get(signal.selected_setup, Setup("NONE"))


def _action_code(signal: Signal) -> str:
    if signal.chase_warning:
        return "WAIT"
    return {
        "BUY": "NOW",
        "SELL": "NOW",
        "STRONG_LONG": "TRY",
        "STRONG_SHORT": "TRY",
        "WATCH_LONG": "NEAR",
        "WATCH_SHORT": "NEAR",
        "NO_TRADE": "WAIT",
        "INVALID_DATA": "DATA",
    }.get(signal.state, "WAIT")


def _action_token(signal: Signal) -> str:
    code = _action_code(signal)
    if code not in {"NEAR", "TRY", "NOW"}:
        return code
    count = max(1, int(getattr(signal, "action_streak_count", 0) or 0))
    return f"{code}{count}"


def _market_bias(signal: Signal) -> float | None:
    weights = {5: 0.45, 20: 0.35, 60: 0.20}
    valid = [
        signal.windows[minute]
        for minute in DISPLAY_WINDOWS
        if minute in signal.windows and signal.windows[minute].quality == "ok"
    ]
    usable_weight = sum(weights[item.minutes] for item in valid)
    if len(valid) < 2 or usable_weight <= 0:
        return None
    return sum(
        item.score * weights[item.minutes]
        for item in valid
    ) / usable_weight


def _directional_window_agreement(signal: Signal, direction: int) -> tuple[int, int]:
    threshold = max(0.01, signal.noise_pct * 0.30)
    aligned = 0
    opposed = 0
    for minutes in DISPLAY_WINDOWS:
        window = signal.windows.get(minutes)
        if window is None or window.quality != "ok" or window.price_pct is None:
            continue
        directional_move = _directional(window.price_pct, direction)
        if directional_move >= threshold:
            aligned += 1
        elif directional_move <= -threshold:
            opposed += 1
    return aligned, opposed


def _window_color(window: Window) -> str:
    if window.quality != "ok":
        return "🟤"
    if window.score >= 15:
        return "🟢"
    if window.score <= -15:
        return "🔴"
    return "🟡"


def _invalid_code(item: Signal) -> str:
    reason = " ".join(item.reasons).lower()
    if "veraltet" in reason or "stale" in reason:
        return "STALE!"
    if "candle gap" in reason or "kerzenlücke" in reason:
        return "GAP!"
    if "/orderbookorders" in reason or "orderbuch" in reason:
        return "BOOK!"
    if "/candles" in reason or "kerzen" in reason:
        return "CND!"
    return "DATA!"


WARNING_DISPLAY_PRIORITY = {
    "K!": 90,   # execution cost can invalidate the trade immediately
    "L!": 88,   # insufficient depth/liquidity
    "CH!": 86,  # late/chased entry
    "F!": 80,   # missing/adverse funding
    "RS!": 78,  # relative reversal weakness
    "R!": 72,   # opposing multi-week regime
    "B!": 68,   # weak BTC context
    "V!": 60,   # weak/uneven tape
}


HEADER_OBSERVATION_EVENT_KINDS = {
    "SECURITY", "NETWORK", "MARKET_SHOCK", "UNLOCK", "SUPPLY",
    "ETF", "ETF_FLOW", "EXPIRY", "UPGRADE", "MAINTENANCE",
    "GOVERNANCE", "NEWS",
}


def _header_observation_event_code(item: Signal, config: Mapping[str, Any]) -> str:
    """Return one verified event/news code worth watching in the compact top row."""
    code = str(item.event_display_code or "")
    if not code:
        return ""

    kind = str(item.event_kind or "").upper().strip()
    unlock_match = re.search(r"U(?:!|0D|\d+D|@\d{2}(?::\d{2})?)", code)
    unlock_code = unlock_match.group(0) if unlock_match else ""

    # Acute security/network/shock information outranks everything else. If an
    # unlock was appended to that label, show only the acute code in the top
    # row; the full event context remains available elsewhere.
    if kind in {"SECURITY", "NETWORK", "MARKET_SHOCK"}:
        if unlock_code:
            primary = code.replace(unlock_code, "", 1)
            return primary or code
        return code

    # A verified unlock outranks lower-priority coin events and is the most
    # useful compact supply warning for this radar row.
    if unlock_code:
        return unlock_code

    if kind in {"UNLOCK", "SUPPLY", "ETF", "ETF_FLOW"}:
        return code
    if kind in HEADER_OBSERVATION_EVENT_KINDS and (
        float(item.event_risk) >= float(config.get("header_observation_min_event_risk", 25.0))
        or float(item.event_priority) >= float(config.get("header_observation_min_event_priority", 65.0))
    ):
        return code
    return ""


def _warning_codes(item: Signal, config: Mapping[str, Any]) -> list[str]:
    if item.state == "INVALID_DATA":
        return [_invalid_code(item)]
    result: list[str] = []
    if item.event_block_new and item.event_code and not item.event_is_global:
        result.append(item.event_code)
    if item.tape_quality < float(config.get("minimum_tape_quality", 68)) or item.volume_confirmation < 38:
        result.append("V!")
    if item.liquidity_score < 58:
        result.append("L!")
    cost_limit = float(config.get("max_roundtrip_cost_pct", 0.10))
    if item.cost_pct is None or item.cost_pct > cost_limit:
        result.append("K!")
    if item.symbol != "BTC" and item.btc_context is not None and item.btc_context < 40:
        result.append("B!")
    regime_warning = float((config.get("regime") or {}).get("warning_threshold_points", -4.0))
    if item.regime_available and item.regime_modifier <= regime_warning:
        result.append("R!")
    if item.selected_setup == "REVERSAL" and item.reversal.relative_opposition:
        result.append("RS!")
    if item.selected_setup == "EARLY" and item.chase_warning:
        result.append("CH!")
    funding_watch = float(config.get("funding_watch_hourly_pct", 0.015))
    if item.funding_hourly_pct is None:
        result.append("F!")
    elif _directional(item.funding_hourly_pct, _direction(item.direction)) > funding_watch:
        result.append("F!")
    unique = list(dict.fromkeys(code for code in result if code))
    event_code = item.event_code if item.event_code in unique else None
    ordinary = [code for code in unique if code != event_code]
    ordinary.sort(key=lambda code: WARNING_DISPLAY_PRIORITY.get(code, 50), reverse=True)
    return ([event_code] if event_code else []) + ordinary


def _header_observation_warnings(item: Signal, config: Mapping[str, Any]) -> list[str]:
    """Only compact risks useful for observation; detailed execution warnings stay below."""
    if item.state == "INVALID_DATA":
        return [_invalid_code(item)]
    allowed = {"CH!", "R!", "F!", "RS!"}
    event = str(item.event_display_code or "")
    return [code for code in _warning_codes(item, config) if code in allowed and code not in event][:2]


class LighterMonitor:
    def __init__(self, config: Mapping[str, Any], client: LighterClient | None = None) -> None:
        self.config = config
        self._validate_config()
        self.client = client or LighterClient(
            str(config.get("lighter_base_url", "https://mainnet.zklighter.elliot.ai/api/v1")),
            float(config.get("request_timeout_seconds", 15)),
            int(config.get("api_retry_count", 3)),
            int(config.get("closed_candle_delay_seconds", 8)),
            int(config.get("lighter_request_limit_per_minute", 54)),
        )
        self.last_signals: list[Signal] = []
        self.last_snapshots: dict[str, dict[str, Any]] = {}
        self.last_incidents: IncidentSnapshot | None = None
        self.last_header_event_symbols: tuple[str, ...] = ()
        self.generated_at = datetime.now(timezone.utc)

    def _validate_config(self) -> None:
        symbols = [str(value).upper() for value in self.config.get("candidate_symbols", [])]
        if not symbols or len(symbols) != len(set(symbols)):
            raise ValueError("candidate_symbols muss eindeutig und nicht leer sein")
        aliases = self.config.get("aliases") or {}
        invalid_aliases = [
            symbol for symbol in symbols
            if len(str(aliases.get(symbol, symbol[:3])).upper()) != 3
        ]
        if invalid_aliases:
            raise ValueError(f"Coin-Kürzel müssen drei Zeichen haben: {invalid_aliases}")
        alias_values = [str(aliases.get(symbol, symbol[:3])).upper() for symbol in symbols]
        if len(alias_values) != len(set(alias_values)):
            raise ValueError("Coin-Kürzel müssen im Kandidatenpool eindeutig sein")
        extra_aliases = set(str(key).upper() for key in aliases) - set(symbols)
        if extra_aliases:
            raise ValueError(f"Display-Aliase außerhalb des Kandidatenpools: {sorted(extra_aliases)}")
        event_aliases = self.config.get("event_symbol_aliases") or {}
        invalid_event_targets = sorted({
            str(value).upper() for value in event_aliases.values()
            if str(value).upper() not in set(symbols)
        })
        if invalid_event_targets:
            raise ValueError(f"Event-Aliase zeigen außerhalb des Kandidatenpools: {invalid_event_targets}")
        summary_count = int(self.config.get("summary_coin_count", 5))
        summary_anchor = str(self.config.get("summary_anchor_symbol", "BTC")).upper()
        minimum_details = int(self.config.get("minimum_detail_count", 1))
        maximum_details = int(self.config.get("maximum_detail_count", 4))
        if not 1 <= summary_count <= len(symbols):
            raise ValueError("summary_coin_count liegt außerhalb der Kandidatenzahl")
        if summary_anchor not in symbols:
            raise ValueError("summary_anchor_symbol muss im Kandidatenpool liegen")
        if not 1 <= minimum_details <= maximum_details <= len(symbols):
            raise ValueError("Detailzeilen-Konfiguration ist ungültig")
        line_limit = int(self.config.get("discord_max_codepoints_per_line", 34))
        display_limit = int(
            self.config.get("discord_max_display_columns_per_line", line_limit + 4)
        )
        header_line_limit = int(
            self.config.get("discord_max_header_codepoints_per_line", line_limit)
        )
        header_display_limit = int(
            self.config.get(
                "discord_max_header_display_columns_per_line",
                min(display_limit, header_line_limit + 4),
            )
        )
        event_line_limit = int(
            self.config.get("discord_max_event_codepoints_per_line", header_line_limit)
        )
        event_display_limit = int(
            self.config.get(
                "discord_max_event_display_columns_per_line",
                min(header_display_limit, event_line_limit + 4),
            )
        )
        if not 34 <= line_limit <= 2_000:
            raise ValueError("Discord-Zeilenlimit ist ungueltig")
        if not line_limit <= display_limit <= 2_000:
            raise ValueError("Discord-Anzeigebreitenlimit ist ungueltig")
        if not 34 <= header_line_limit <= line_limit:
            raise ValueError("Discord-Kopfzeilenlimit ist ungueltig")
        if not header_line_limit <= header_display_limit <= display_limit:
            raise ValueError("Discord-Kopfzeilenbreite ist ungueltig")
        if not 34 <= event_line_limit <= header_line_limit:
            raise ValueError("Discord-Ereigniszeilenlimit ist ungueltig")
        if not event_line_limit <= event_display_limit <= header_display_limit:
            raise ValueError("Discord-Ereignisanzeigebreite ist ungueltig")
        if event_line_limit < summary_count * 5 - 1:
            raise ValueError("Discord-Ereigniszeilenlimit ist für die Top-Zeile zu klein")
        if header_line_limit < summary_count * 5 - 1:
            raise ValueError("Discord-Kopfzeilenlimit ist für die Top-Zeile zu klein")
        if line_limit < summary_count * 5 - 1:
            raise ValueError("Discord-Zeilenlimit ist für die Top-Zeile zu klein")
        watch = float(self.config.get("watch_trade_readiness", 56))
        strong = float(self.config.get("strong_trade_readiness", 64))
        immediate = float(self.config.get("immediate_trade_readiness", 76))
        if not 0 < watch < strong < immediate <= 100:
            raise ValueError("Readiness-Schwellen sind nicht logisch geordnet")
        flip_guard = int(self.config.get("signal_flip_guard_minutes", 10))
        flip_try = int(self.config.get("signal_flip_try_confirmed_minutes", 2))
        flip_now = int(self.config.get("signal_flip_now_confirmed_minutes", 3))
        reversal_cap = float(
            self.config.get("unconfirmed_reversal_max_readiness", strong - 1.0)
        )
        if not 1 <= flip_guard <= 60 or not 2 <= flip_try < flip_now <= 10:
            raise ValueError("Richtungswechsel-Schutz ist ungültig")
        if not watch <= reversal_cap < strong:
            raise ValueError("W?-Readiness-Cap muss zwischen NEAR und TRY liegen")
        funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
        funding_hard = float(self.config.get("funding_hard_hourly_pct", 0.05))
        if not 0 <= funding_watch < funding_hard:
            raise ValueError("Funding-Schwellen sind nicht logisch geordnet")
        setup_thresholds = (
            "early_minimum_setup_score",
            "early_minimum_volume_score",
            "trend_minimum_setup_score",
            "trend_minimum_context_strength",
            "trend_minimum_volume_score",
            "reversal_minimum_setup_score",
            "btc_early_immediate_breadth_score",
            "btc_trend_immediate_breadth_score",
            "btc_reversal_immediate_breadth_score",
            "minimum_tape_quality",
            "detail_attention_threshold",
        )
        if any(not 0 <= float(self.config.get(key, 100)) <= 100 for key in setup_thresholds):
            raise ValueError("Setup- und Qualitätswerte müssen zwischen null und 100 liegen")
        if not 0 < float(self.config.get("early_max_consumed_fraction", 0.66)) < 1:
            raise ValueError("early_max_consumed_fraction muss zwischen null und eins liegen")
        if not 0 < float(self.config.get("early_min_efficiency", 0.52)) <= 1:
            raise ValueError("early_min_efficiency muss zwischen null und eins liegen")
        early_max_age = int(self.config.get("early_max_age_minutes", 2))
        if not 0 <= early_max_age <= 4:
            raise ValueError("early_max_age_minutes muss zwischen null und vier liegen")
        immediate_age = int(self.config.get("early_immediate_max_age_minutes", 1))
        immediate_used = float(self.config.get("early_immediate_max_consumed_fraction", 0.50))
        if not 0 <= immediate_age <= early_max_age:
            raise ValueError("early_immediate_max_age_minutes ist ungültig")
        if not 0 < immediate_used <= float(self.config.get("early_max_consumed_fraction", 0.66)):
            raise ValueError("early_immediate_max_consumed_fraction ist ungültig")
        approach_multiple = float(self.config.get("early_approach_noise_multiple", 2.0))
        probe_multiple = float(self.config.get("early_probe_noise_multiple", 0.60))
        probe_used = float(self.config.get("early_probe_max_consumed_fraction", 0.35))
        if not 0.8 <= approach_multiple <= 4.0:
            raise ValueError("early_approach_noise_multiple ist ungültig")
        if not 0.2 <= probe_multiple < approach_multiple:
            raise ValueError("early_probe_noise_multiple ist ungültig")
        if not 0.10 <= probe_used < immediate_used:
            raise ValueError("early_probe_max_consumed_fraction ist ungültig")
        extremity_warning = float(self.config.get("extremity_chase_warning", 55.0))
        extremity_block = float(self.config.get("extremity_chase_block", 72.0))
        extremity_penalty = float(self.config.get("extremity_chase_penalty", 8.0))
        if not 0 < extremity_warning < extremity_block <= 100 or not 0 <= extremity_penalty <= 20:
            raise ValueError("Extremity-Schwellen sind nicht logisch geordnet")
        early_chase_warning = float(self.config.get("early_chase_warning", 35.0))
        early_chase_block = float(self.config.get("early_chase_block", 50.0))
        early_chase_penalty = float(self.config.get("early_chase_penalty", 10.0))
        if not 0 < early_chase_warning < early_chase_block <= 100 or not 0 <= early_chase_penalty <= 20:
            raise ValueError("E-Anti-Chase-Schwellen sind nicht logisch geordnet")
        if not 0 <= float(self.config.get("reversal_now_min_extremity", 18.0)) <= 60:
            raise ValueError("W-Extremitätsminimum ist ungültig")
        if not 1 <= int(self.config.get("early_min_volume_consistency", 2)) <= 4:
            raise ValueError("early_min_volume_consistency muss zwischen eins und vier liegen")
        minimum_reversal = float(self.config.get("hard_reversal_min_move_pct", 0.20))
        immediate_reversal = float(self.config.get("reversal_immediate_min_move_pct", 0.75))
        rejection = float(self.config.get("reversal_min_rejection_fraction", 0.30))
        max_recovery = float(self.config.get("reversal_max_entry_recovery_fraction", 0.68))
        late_override = float(self.config.get("reversal_late_recovery_override_move_pct", 1.0))
        reversal_confirmations = int(
            self.config.get("reversal_immediate_min_confirmations", 2)
        )
        reversal_agreement = int(
            self.config.get("reversal_immediate_min_window_agreement", 2)
        )
        reversal_opposed = int(
            self.config.get("reversal_immediate_max_opposed_windows", 1)
        )
        reclaim_fraction = float(self.config.get("reversal_structural_reclaim_fraction", 0.24))
        new_extreme_tolerance = float(self.config.get("reversal_new_extreme_tolerance_noise", 0.18))
        post_event_closes = int(self.config.get("reversal_min_post_event_closes", 1))
        if (
            not 0 < minimum_reversal <= immediate_reversal <= late_override
            or not 0 <= rejection <= 1
            or not 0 < max_recovery < 1
            or not 1 <= reversal_confirmations <= 3
            or not 1 <= reversal_agreement <= len(DISPLAY_WINDOWS)
            or not 0 <= reversal_opposed < len(DISPLAY_WINDOWS)
            or not 0.10 <= reclaim_fraction <= 0.60
            or not 0 <= new_extreme_tolerance <= 1.0
            or not 1 <= post_event_closes <= 3
        ):
            raise ValueError("Wende-Schwellen sind nicht logisch geordnet")
        positive_keys = (
            "execution_quote_usdc", "minimum_volume_24h_usdc",
            "minimum_open_interest_usdc", "max_roundtrip_cost_pct",
            "candle_count", "parallel_requests", "request_timeout_seconds",
            "early_minimum_expected_edge_pct", "early_cost_edge_multiple",
        )
        if any(float(self.config.get(key, 0)) <= 0 for key in positive_keys):
            raise ValueError("Ausführungs- und Datenparameter müssen positiv sein")
        timing_scores = (
            "detail_timing_confirmation_min_score",
            "radar_min_tape_quality",
        )
        if any(not 0 <= float(self.config.get(key, -1)) <= 100 for key in timing_scores):
            raise ValueError("Radar-/Timing-Scores müssen zwischen null und 100 liegen")
        radar_weights = [
            float(self.config.get("radar_speed_weight", 0.38)),
            float(self.config.get("radar_activity_weight", 0.42)),
            float(self.config.get("radar_extremity_weight", 0.20)),
        ]
        if any(value < 0 for value in radar_weights) or sum(radar_weights) <= 0:
            raise ValueError("Radar-Gewichte sind ungültig")
        radar_min_leverage = float(self.config.get("radar_min_platform_leverage", 5.0))
        if not 1.0 <= radar_min_leverage <= 100.0:
            raise ValueError("radar_min_platform_leverage ist ungültig")
        speed_floor = float(self.config.get("swing_speed_absolute_floor_pct", 0.025))
        speed_strong = float(self.config.get("swing_speed_strong_pct", 0.16))
        if not 0 < speed_floor < speed_strong <= 5.0:
            raise ValueError("Swing-Speed-Schwellen sind ungültig")
        if not 5 <= int(self.config.get("swing_speed_lookback_minutes", 12)) <= 30:
            raise ValueError("swing_speed_lookback_minutes ist ungültig")
        if not 3 <= int(self.config.get("swing_activity_lookback_minutes", 5)) <= 15:
            raise ValueError("swing_activity_lookback_minutes ist ungültig")
        if not 10 <= int(self.config.get("swing_two_sided_lookback_minutes", 20)) <= 40:
            raise ValueError("swing_two_sided_lookback_minutes ist ungültig")
        if bool(self.config.get("springer_enabled", True)):
            springer_minutes = int(self.config.get("springer_minute_lookback_minutes", 300))
            springer_min = int(self.config.get("springer_min_contiguous_minutes", 180))
            springer_days = int(self.config.get("springer_daily_lookback_days", 30))
            if not 180 <= springer_minutes <= 480:
                raise ValueError("springer_minute_lookback_minutes ist ungültig")
            if not 120 <= springer_min <= springer_minutes:
                raise ValueError("springer_min_contiguous_minutes ist ungültig")
            if not 10 <= springer_days <= 30:
                raise ValueError("springer_daily_lookback_days ist ungültig")
            if int(self.config.get("candle_count", 360)) < springer_minutes + 1:
                raise ValueError("candle_count reicht für J nicht aus")
            if int((self.config.get("regime") or {}).get("daily_candle_count", 40)) < springer_days:
                raise ValueError("daily_candle_count reicht für J nicht aus")
            classes = self.config.get("springer_classes") or {}
            class_symbols = [str(symbol).upper() for values in classes.values() for symbol in (values or [])]
            non_btc = [symbol for symbol in symbols if symbol != "BTC"]
            if set(class_symbols) != set(non_btc) or len(class_symbols) != len(set(class_symbols)):
                raise ValueError("springer_classes muss jeden Altcoin exakt einmal enthalten")
        pin_lookback = int(self.config.get("btc_pin_lookback_minutes", 60))
        pin_step = float(self.config.get("btc_pin_level_step_usd", 1000.0))
        pin_return_band = float(self.config.get("btc_pin_return_band_step_fraction", 0.18))
        pin_return_band_max = float(self.config.get("btc_pin_return_band_max_step_fraction", 0.30))
        pin_noise_multiple = float(self.config.get("btc_pin_noise_multiple", 3.0))
        pin_return_minutes = int(self.config.get("btc_pin_return_minutes", 5))
        pin_min_coverage = float(self.config.get("btc_pin_min_coverage", 0.90))
        pin_max_gap = float(self.config.get("btc_pin_max_gap_minutes", 3.0))
        if not 30 <= pin_lookback <= 180:
            raise ValueError("btc_pin_lookback_minutes ist ungültig")
        if not 100.0 <= pin_step <= 10_000.0:
            raise ValueError("btc_pin_level_step_usd ist ungültig")
        if not 0.05 <= pin_return_band < pin_return_band_max <= 0.45:
            raise ValueError("BTC-Pin-Rückkehrband ist ungültig")
        if not 0.0 <= pin_noise_multiple <= 10.0:
            raise ValueError("btc_pin_noise_multiple ist ungültig")
        if not 1 <= pin_return_minutes <= 15:
            raise ValueError("btc_pin_return_minutes ist ungültig")
        if not 0.75 <= pin_min_coverage <= 1.0:
            raise ValueError("btc_pin_min_coverage ist ungültig")
        if not 1.0 <= pin_max_gap <= 5.0:
            raise ValueError("btc_pin_max_gap_minutes ist ungültig")
        if int(self.config.get("candle_count", 360)) < 200:
            raise ValueError("candle_count muss mindestens 200 betragen")
        request_limit = int(self.config.get("lighter_request_limit_per_minute", 54))
        if not 10 <= request_limit <= 60:
            raise ValueError("lighter_request_limit_per_minute muss zwischen 10 und 60 liegen")
        daily_batch = int(self.config.get("daily_candle_refresh_batch_size", 4))
        if not 1 <= daily_batch <= 8:
            raise ValueError("daily_candle_refresh_batch_size ist ungültig")
        worst_normal_requests = 2 + 2 * len(symbols) + daily_batch
        if worst_normal_requests > request_limit:
            raise ValueError("Lighter-Requestbudget reicht für Pool plus Daily-Refresh nicht aus")
        breadth_symbols = [str(value).upper() for value in self.config.get("btc_breadth_symbols", [])]
        if len(breadth_symbols) != len(set(breadth_symbols)):
            raise ValueError("btc_breadth_symbols muss eindeutig sein")
        if len(breadth_symbols) < 3 or "BTC" in breadth_symbols:
            raise ValueError("btc_breadth_symbols braucht mindestens drei Nicht-BTC-Märkte")
        if not set(breadth_symbols).issubset(set(symbols)):
            raise ValueError("btc_breadth_symbols müssen im Kandidatenpool liegen")
        regime = self.config.get("regime") or {}
        if bool(regime.get("enabled", True)):
            horizons = [int(value) for value in regime.get("horizons_days", [7, 14, 30])]
            if horizons != [7, 14, 30]:
                raise ValueError("Regime-Horizonte müssen exakt 7/14/30 Tage sein")
            maximum_modifier = float(regime.get("maximum_modifier_points", 10.0))
            if not 0 < maximum_modifier <= 10:
                raise ValueError("Regime-Modifier muss zwischen 0 und 10 liegen")

    def _analyse(
        self,
        market: Mapping[str, Any],
        funding: float | None,
        candles: list[Mapping[str, Any]],
        book: Mapping[str, Any],
    ) -> Signal:
        symbol = str(market["symbol"]).upper()
        aliases = self.config.get("aliases") or {}
        signal = Signal(symbol=symbol, alias=str(aliases.get(symbol, symbol[:3])).upper())
        signal.volume_24h = _f(market.get("daily_quote_token_volume"))
        reference_price = _f(
            market.get("mark_price"),
            _f(market.get("last_trade_price")),
        )
        signal.open_interest_usd = _f(market.get("open_interest"))
        signal.volume_oi = (
            signal.volume_24h / signal.open_interest_usd
            if signal.open_interest_usd > 0
            else None
        )
        signal.funding_8h_pct = None if funding is None else funding * 100.0
        signal.funding_hourly_pct = (
            None
            if signal.funding_8h_pct is None
            else signal.funding_8h_pct / FUNDING_NORMALIZATION_HOURS
        )
        signal.live_price = reference_price
        signal.price = (
            _f(candles[-1].get("c"), reference_price)
            if candles
            else reference_price
        )
        signal.candle_timestamp_ms = _timestamp_ms(candles[-1]) if candles else 0
        signal.min_quote_amount = max(0.0, _f(market.get("min_quote_amount")))
        minimum_margin_fraction = _f(market.get("min_initial_margin_fraction"))
        signal.platform_max_leverage = (
            10_000.0 / minimum_margin_fraction
            if minimum_margin_fraction > 0
            else 0.0
        )
        signal.maintenance_margin_pct = max(
            0.0,
            _f(market.get("maintenance_margin_fraction")) / 100.0,
        )
        signal.taker_fee_pct = (
            max(0.0, _f(market.get("taker_fee")))
            if market.get("is_taker_fee_enabled", True) is not False
            else 0.0
        )
        execution_quote = float(self.config.get("execution_quote_usdc", 50))
        signal.long_cost_pct = _roundtrip_cost(book, execution_quote, 1)
        signal.short_cost_pct = _roundtrip_cost(book, execution_quote, -1)
        signal.cost_pct = None
        signal.windows = {
            minutes: _window(candles, minutes)
            for minutes in ANALYSIS_WINDOWS
        }
        display_good = [
            signal.windows[minutes]
            for minutes in DISPLAY_WINDOWS
            if signal.windows[minutes].quality == "ok"
        ]
        signal.data_quality = len(display_good) / len(DISPLAY_WINDOWS) * 100.0
        if len(display_good) < 2:
            signal.reasons.append("zu wenige gültige Fenster")
            return signal

        minimum_volume = float(self.config.get("minimum_volume_24h_usdc", 500_000))
        minimum_oi = float(self.config.get("minimum_open_interest_usdc", 100_000))
        signal.activity_score = _activity_score(
            signal.volume_24h,
            signal.open_interest_usd,
            signal.volume_oi,
            minimum_volume,
            minimum_oi,
        )
        signal.tape_quality, signal.volume_coverage, signal.volume_spike_share = _tape_quality(candles)
        signal.liquidity_score = _clamp(
            signal.activity_score * 0.72 + signal.tape_quality * 0.28
        )
        cost_limit = float(self.config.get("max_roundtrip_cost_pct", 0.15))

        noise = _robust_noise(candles)
        signal.noise_pct = noise
        signal.early = _assess_early(
            candles,
            noise,
            current_price=reference_price,
            max_age_minutes=int(self.config.get("early_max_age_minutes", 2)),
            max_consumed_fraction=float(self.config.get("early_max_consumed_fraction", 0.66)),
            minimum_efficiency=float(self.config.get("early_min_efficiency", 0.52)),
            minimum_volume_consistency=int(self.config.get("early_min_volume_consistency", 2)),
            approach_noise_multiple=float(self.config.get("early_approach_noise_multiple", 2.0)),
            probe_noise_multiple=float(self.config.get("early_probe_noise_multiple", 0.60)),
            probe_max_consumed_fraction=float(self.config.get("early_probe_max_consumed_fraction", 0.35)),
        )
        signal.trend = _assess_trend(candles, signal.windows, noise, self.config)
        signal.reversal = _assess_reversal(
            candles,
            noise,
            float(self.config.get("hard_reversal_min_move_pct", 0.20)),
            self.config,
        )
        setups = (signal.early, signal.trend, signal.reversal)
        selected = max(
            setups,
            key=lambda item: (
                *_setup_priority(item),
                3 if item.kind == "EARLY" and item.phase == "ready" else
                2 if item.kind == "REVERSAL" and item.phase == "ready" else 0,
            ),
        )
        signal.selected_setup = selected.kind if selected.phase != "none" else "NONE"
        raw_setup = selected.score if selected.phase != "none" else min(selected.score, 42.0)
        valid_volume_ratios = [
            item.volume_ratio
            for item in display_good
            if item.volume_ratio is not None
        ]
        general_volume_score = (
            _clamp(50.0 + math.log(max(_mean(valid_volume_ratios), 1e-9)) * 45.0)
            if valid_volume_ratios
            else 0.0
        )
        signal.volume_confirmation = (
            selected.volume_score
            if selected.phase != "none"
            else general_volume_score
        )

        if selected.direction and selected.phase != "none":
            signal.direction = max(1.0, selected.score) * selected.direction
        else:
            weights = {5: 0.52, 20: 0.30, 60: 0.18}
            usable_weight = sum(weights[item.minutes] for item in display_good)
            signal.direction = (
                sum(
                    item.score * weights[item.minutes]
                    for item in display_good
                ) / usable_weight
                if usable_weight
                else 0.0
            )

        direction = _direction(signal.direction)
        signal.cost_pct = signal.long_cost_pct if direction > 0 else signal.short_cost_pct
        signal.execution_score = (
            0.0
            if signal.cost_pct is None
            else _clamp(100.0 - signal.cost_pct / max(cost_limit, 1e-9) * 100.0)
        )
        invalidation = selected.invalidation_price
        if invalidation is not None and signal.price > 0:
            valid_side = (
                (direction > 0 and invalidation < signal.price)
                or (direction < 0 and invalidation > signal.price)
            )
            if valid_side:
                signal.technical_stop_price = float(invalidation)
                signal.technical_stop_pct = abs(_pct(signal.price, float(invalidation)))
        active_opposites = [
            item
            for item in setups
            if item is not selected
            and item.phase in {"ready", "strong"}
            and item.direction
            and item.direction != direction
        ]
        setup_conflict = any(
            item.phase == "ready" and item.score >= selected.score - 12.0
            for item in active_opposites
        )
        countertrend_reversal = (
            selected.kind == "REVERSAL"
            and selected.phase == "ready"
            and signal.trend.direction
            and signal.trend.direction != direction
            and signal.trend.phase in {"ready", "strong", "forming"}
            and signal.trend.score >= 62.0
            and selected.rejection_fraction < 0.25
        )
        setup_conflict = setup_conflict or countertrend_reversal
        same_direction_support = sum(
            item.phase in {"ready", "strong", "forming"}
            and item.direction == direction
            for item in setups
        )

        movement = _mean(
            min(100.0, abs(item.price_pct or 0.0) * 82.0)
            for item in display_good
        )
        stability = _clamp(
            48.0
            + selected.confirmations * 14.0
            + (18.0 if selected.phase == "ready" else 0.0)
            + (8.0 if same_direction_support >= 2 else 0.0)
        )
        if (
            selected.kind == "REVERSAL"
            and selected.phase == "ready"
            and selected.event_strength >= 7.0
        ):
            stability = max(stability, 88.0)
        signal.opportunity = _clamp(
            raw_setup * 0.52
            + signal.activity_score * 0.10
            + signal.execution_score * 0.10
            + movement * 0.10
            + stability * 0.08
            + signal.tape_quality * 0.10
        )
        signal.confidence = _clamp(
            raw_setup * 0.46
            + signal.data_quality * 0.10
            + signal.execution_score * 0.10
            + signal.activity_score * 0.07
            + signal.volume_confirmation * 0.08
            + stability * 0.10
            + signal.tape_quality * 0.09
        )
        signal.trade_readiness = _clamp(
            raw_setup * 0.53
            + signal.volume_confirmation * 0.10
            + signal.data_quality * 0.06
            + signal.execution_score * 0.08
            + signal.activity_score * 0.05
            + stability * 0.07
            + signal.tape_quality * 0.11
            + (4.0 if same_direction_support >= 2 else 0.0)
            - (15.0 if setup_conflict else 0.0)
        )
        if selected.kind == "TREND":
            signal.opportunity = _clamp(
                signal.opportunity - float(self.config.get("trend_attention_penalty", 5)) * 0.4
            )
            signal.confidence = _clamp(
                signal.confidence - float(self.config.get("trend_confidence_penalty", 3))
            )
            signal.trade_readiness = _clamp(
                signal.trade_readiness - float(self.config.get("trend_readiness_penalty", 5))
            )

        funding_watch = float(self.config.get("funding_watch_hourly_pct", 0.015))
        funding_hard = float(self.config.get("funding_hard_hourly_pct", 0.05))
        funding_against = (
            signal.funding_hourly_pct is not None
            and _directional(signal.funding_hourly_pct, direction) > 0
        )
        funding_magnitude = (
            _directional(signal.funding_hourly_pct or 0.0, direction)
            if funding_against
            else 0.0
        )
        if funding_magnitude > funding_watch:
            setup_factor = {
                "EARLY": 0.65,
                "TREND": 0.8,
                "REVERSAL": 0.25,
            }.get(selected.kind, 0.6)
            signal.trade_readiness -= _clamp(
                (funding_magnitude - funding_watch)
                / max(funding_hard - funding_watch, 1e-9)
                * 14.0
                * setup_factor,
                0.0,
                14.0,
            )
        signal.trade_readiness = _clamp(signal.trade_readiness)

        executable = signal.cost_pct is not None and signal.cost_pct <= cost_limit
        meets_market_minimum = execution_quote >= _f(market.get("min_quote_amount"))
        enough_volume = signal.volume_24h >= minimum_volume
        enough_oi = signal.open_interest_usd >= minimum_oi
        tape_ok = signal.tape_quality >= float(self.config.get("minimum_tape_quality", 68))
        funding_missing = signal.funding_hourly_pct is None
        funding_block = (
            funding_magnitude > funding_hard
            and selected.kind not in {"REVERSAL"}
        )
        all_display_windows = len(display_good) == len(DISPLAY_WINDOWS)
        immediate_threshold = float(self.config.get("immediate_trade_readiness", 74))
        strong_threshold = float(self.config.get("strong_trade_readiness", 66))
        watch_threshold = float(self.config.get("watch_trade_readiness", 50))
        fresh_limit = (
            int(self.config.get("early_max_age_minutes", 2))
            if selected.kind == "EARLY" else 3
        )
        fresh_entry = selected.age_minutes is None or selected.age_minutes <= fresh_limit
        setup_minimum = float(
            self.config.get(
                {
                    "EARLY": "early_minimum_setup_score",
                    "TREND": "trend_minimum_setup_score",
                    "REVERSAL": "reversal_minimum_setup_score",
                }.get(selected.kind, ""),
                100.0,
            )
        )
        setup_is_precise = selected.score >= setup_minimum
        if selected.kind == "EARLY":
            effective_cost = signal.cost_pct if signal.cost_pct is not None else cost_limit
            expected_edge_required = max(
                float(self.config.get("early_minimum_expected_edge_pct", 0.10)),
                effective_cost * float(self.config.get("early_cost_edge_multiple", 3.2)),
            )
            setup_is_precise = (
                setup_is_precise
                and selected.volume_score >= float(self.config.get("early_minimum_volume_score", 50))
                and selected.expected_edge_pct >= expected_edge_required
                and selected.rejection_fraction >= float(self.config.get("early_min_efficiency", 0.52))
                and selected.recovery_fraction <= float(self.config.get("early_max_consumed_fraction", 0.66))
                and selected.confirmations >= int(self.config.get("early_min_volume_consistency", 2))
            )
        elif selected.kind == "TREND":
            effective_cost = (
                signal.cost_pct
                if signal.cost_pct is not None
                else cost_limit
            )
            expected_edge_required = max(
                float(
                    self.config.get(
                        "trend_minimum_expected_edge_pct",
                        0.10,
                    )
                ),
                effective_cost
                * float(self.config.get("trend_cost_edge_multiple", 3.0)),
            )
            setup_is_precise = (
                setup_is_precise
                and selected.event_strength >= float(
                    self.config.get("trend_minimum_context_strength", 84)
                )
                and selected.volume_score >= float(
                    self.config.get("trend_minimum_volume_score", 50)
                )
                and selected.expected_edge_pct >= expected_edge_required
            )
        elif selected.kind == "REVERSAL":
            recovery_limit = float(
                self.config.get(
                    "reversal_max_entry_recovery_fraction",
                    0.68,
                )
            )
            late_recovery_override = (
                selected.move_pct >= float(
                    self.config.get(
                        "reversal_late_recovery_override_move_pct",
                        1.0,
                    )
                )
                and selected.age_minutes is not None
                and selected.age_minutes <= 2
            )
            aligned_windows, opposed_windows = _directional_window_agreement(
                signal, direction
            )
            setup_is_precise = (
                setup_is_precise
                and selected.move_pct >= float(
                    self.config.get("reversal_immediate_min_move_pct", 0.75)
                )
                and selected.rejection_fraction >= float(
                    self.config.get("reversal_min_rejection_fraction", 0.30)
                )
                and selected.confirmations >= int(
                    self.config.get("reversal_immediate_min_confirmations", 2)
                )
                and aligned_windows >= int(
                    self.config.get("reversal_immediate_min_window_agreement", 2)
                )
                and opposed_windows <= int(
                    self.config.get("reversal_immediate_max_opposed_windows", 1)
                )
                and selected.age_minutes is not None
                and selected.age_minutes >= 1
                and selected.structural_reclaim
                and selected.relative_confirmed
                and not selected.new_extreme_after_event
                and (
                    selected.recovery_fraction <= recovery_limit
                    or late_recovery_override
                )
            )
        hard_block = (
            not executable
            or not meets_market_minimum
            or not enough_volume
            or not enough_oi
            or not tape_ok
            or funding_missing
            or funding_block
        )

        if not executable or not meets_market_minimum:
            signal.state = "NO_TRADE"
            signal.reasons.append("Orderbuchkosten blockieren")
        elif not enough_volume or not enough_oi:
            signal.state = "NO_TRADE"
            signal.reasons.append("Liquidität/OI blockiert")
        elif not tape_ok:
            signal.state = "NO_TRADE"
            signal.reasons.append("Volumenverlauf zu lückenhaft/sprunghaft")
        elif funding_missing:
            signal.state = "NO_TRADE"
            signal.reasons.append("Funding nicht verfügbar")
        elif funding_block:
            signal.state = "NO_TRADE"
            signal.reasons.append("Funding blockiert Richtung")
        elif (
            selected.phase == "ready"
            and all_display_windows
            and fresh_entry
            and setup_is_precise
            and not setup_conflict
            and (selected.kind != "TREND" or bool(self.config.get("trend_immediate_enabled", False)))
            and signal.trade_readiness >= immediate_threshold
        ):
            signal.state = "BUY" if direction > 0 else "SELL"
            signal.reasons.append(f"{_setup_code(signal)} sofort bereit")
        elif (
            not hard_block
            and selected.phase in {"ready", "strong"}
            and signal.trade_readiness >= strong_threshold
        ):
            signal.state = "STRONG_LONG" if direction > 0 else "STRONG_SHORT"
            signal.reasons.append(f"{_setup_code(signal)} stark, nicht sofort")
        elif (
            not hard_block
            and selected.phase in {"ready", "strong", "forming"}
            and signal.trade_readiness >= watch_threshold
        ):
            signal.state = "WATCH_LONG" if direction > 0 else "WATCH_SHORT"
            signal.reasons.append(f"{_setup_code(signal)} im Aufbau")
        else:
            signal.state = "NO_TRADE"
            signal.reasons.append("keine sichere Einstiegsfreigabe")

        if setup_conflict:
            signal.reasons.append("Setups widersprechen sich")
        if selected.exit_hint:
            signal.reasons.append("Setup abgelaufen/aussteigen prüfen")
        if funding_magnitude > funding_watch and not funding_block:
            signal.reasons.append("Funding gegen Richtung")
        signal.base_trade_readiness = float(signal.trade_readiness)
        raw_tier = (
            3 if signal.trade_readiness >= immediate_threshold else
            2 if signal.trade_readiness >= strong_threshold else
            1 if signal.trade_readiness >= watch_threshold else 0
        )
        actual_tier = {
            "BUY": 3, "SELL": 3,
            "STRONG_LONG": 2, "STRONG_SHORT": 2,
            "WATCH_LONG": 1, "WATCH_SHORT": 1,
        }.get(signal.state, 0)
        signal.state_limited_by_setup = bool(not hard_block and actual_tier < raw_tier)
        return signal

    def _apply_btc_context(self, signals: list[Signal]) -> None:
        """Use BTC only as a risk gate; it never creates or upgrades a signal."""
        btc = next(
            (
                item
                for item in signals
                if item.symbol == "BTC" and item.state != "INVALID_DATA"
            ),
            None,
        )
        if btc is None:
            return
        btc_bias = btc.direction
        btc_direction = _direction(btc_bias)
        breadth_symbols = {
            str(value).upper()
            for value in self.config.get("btc_breadth_symbols", [])
        }
        breadth_rows: list[tuple[float, float]] = []
        supporting = 0
        opposing = 0
        for item in signals:
            if (
                item.symbol == "BTC"
                or item.symbol not in breadth_symbols
                or item.state == "INVALID_DATA"
                or item.data_quality < 66.0
                or item.liquidity_score < 40.0
            ):
                continue
            bias = _market_bias(item)
            if bias is None:
                continue
            aligned = _directional(bias, btc_direction)
            weight = 0.65 + _clamp(item.liquidity_score, 0.0, 100.0) / 100.0 * 0.35
            breadth_rows.append((aligned, weight))
            supporting += aligned >= 8.0
            opposing += aligned <= -8.0

        btc_breadth: float | None = None
        if len(breadth_rows) >= 3:
            weighted_alignment = sum(
                _clamp(value, -60.0, 60.0) * weight
                for value, weight in breadth_rows
            ) / sum(weight for _, weight in breadth_rows)
            balance = (supporting - opposing) / len(breadth_rows)
            btc_breadth = _clamp(
                50.0 + weighted_alignment * 0.55 + balance * 22.0
            )
        btc.btc_context = btc_breadth

        if btc.state in {"BUY", "SELL"}:
            breadth_threshold = float(
                self.config.get(
                    {
                        "EARLY": "btc_early_immediate_breadth_score",
                        "TREND": "btc_trend_immediate_breadth_score",
                        "REVERSAL": "btc_reversal_immediate_breadth_score",
                    }.get(btc.selected_setup, ""),
                    100.0,
                )
            )
            if btc_breadth is None or btc_breadth < breadth_threshold:
                btc.state = (
                    "STRONG_LONG"
                    if btc.direction >= 0
                    else "STRONG_SHORT"
                )
                btc.reasons.append("Marktbreite verhindert BTC-Sofortfreigabe")

        for item in signals:
            if item.state == "INVALID_DATA":
                continue
            if item.symbol == "BTC":
                continue
            if abs(btc_bias) < 15.0:
                item.btc_context = 60.0
                continue
            alignment = _directional(btc_bias, _direction(item.direction))
            item.btc_context = (
                100.0 if alignment >= 55.0 else
                82.0 if alignment >= 25.0 else
                60.0 if alignment > -18.0 else
                38.0 if alignment > -50.0 else
                18.0
            )
            if item.btc_context >= 40.0:
                continue
            penalty = 7.0 if item.selected_setup == "REVERSAL" else (9.0 if item.selected_setup == "EARLY" else 12.0)
            item.trade_readiness = _clamp(item.trade_readiness - penalty)
            item.confidence = _clamp(item.confidence - penalty * 0.55)
            reversal_overrides_btc = (
                item.selected_setup == "REVERSAL"
                and item.reversal.move_pct >= float(
                    self.config.get("reversal_btc_override_move_pct", 1.0)
                )
            )
            if item.state in {"BUY", "SELL"} and not reversal_overrides_btc:
                item.state = (
                    "STRONG_LONG"
                    if item.direction >= 0
                    else "STRONG_SHORT"
                )
                item.reasons.append("BTC-Kontext verhindert Sofortfreigabe")

    def _apply_regime_context(
        self,
        signals: list[Signal],
        snapshots: Mapping[str, Mapping[str, Any]],
        daily_candles: Mapping[str, list[Mapping[str, Any]]],
        now: datetime,
    ) -> dict[str, Any]:
        regime_config = self.config.get("regime") or {}
        if not bool(regime_config.get("enabled", True)):
            return {}
        results = calculate_regimes(
            signals=signals,
            minute_snapshots=snapshots,
            daily_candles=daily_candles,
            now_ms=int(now.timestamp() * 1000),
            config=self.config,
        )
        immediate = float(self.config.get("immediate_trade_readiness", 76))
        strong = float(self.config.get("strong_trade_readiness", 64))
        watch = float(self.config.get("watch_trade_readiness", 56))
        early_block = float(regime_config.get("early_opposition_block_points", -6.5))
        warning_threshold = float(regime_config.get("warning_threshold_points", -4.0))

        def downgrade(item: Signal) -> None:
            direction_long = item.direction >= 0
            if item.state in {"BUY", "SELL"} and item.trade_readiness < immediate:
                item.state = "STRONG_LONG" if direction_long else "STRONG_SHORT"
            if item.state in {"STRONG_LONG", "STRONG_SHORT"} and item.trade_readiness < strong:
                item.state = "WATCH_LONG" if direction_long else "WATCH_SHORT"
            if item.state in {"WATCH_LONG", "WATCH_SHORT"} and item.trade_readiness < watch:
                item.state = "NO_TRADE"

        for item in signals:
            result = results.get(item.symbol)
            if result is None or not result.available:
                if result is not None and result.reason:
                    item.reasons.append(result.reason)
                continue
            item.regime_available = True
            item.regime_score = result.score
            item.regime_consistency = result.consistency
            item.regime_modifier = result.modifier
            item.return_7d = result.return_7d
            item.return_14d = result.return_14d
            item.return_30d = result.return_30d
            item.relative_7d = result.relative_7d
            item.relative_14d = result.relative_14d
            item.relative_30d = result.relative_30d
            item.btc_rebound_pct = result.btc_rebound_pct
            item.rebound_participation = result.rebound_participation
            item.relative_drift_60m = result.relative_drift_60m

            modifier = float(result.modifier)
            item.opportunity = _clamp(item.opportunity + modifier * 0.55)
            item.confidence = _clamp(item.confidence + modifier * 0.75)
            item.trade_readiness = _clamp(item.trade_readiness + modifier)

            # A W against a clearly stronger reference-market move remains W?
            # even if the local candle pattern reclaimed its first level. This
            # directly covers relief bounces that lag BTC/the broad pool and roll over.
            if item.selected_setup == "REVERSAL" and item.reversal.phase in {"ready", "strong"}:
                direction = 1 if item.direction >= 0 else -1
                drift_threshold = float(
                    regime_config.get("reversal_relative_drift_warning_pct", 0.30)
                )
                participation_threshold = float(
                    regime_config.get("reversal_min_rebound_participation", 0.45)
                )
                drift_opposition = (
                    result.relative_drift_60m is not None
                    and _directional(float(result.relative_drift_60m), direction) < -drift_threshold
                )
                participation_opposition = (
                    direction > 0
                    and result.rebound_participation is not None
                    and float(result.rebound_participation) < participation_threshold
                )
                item.reversal.relative_participation = result.rebound_participation
                item.reversal.relative_opposition = bool(
                    drift_opposition or participation_opposition
                )
                item.reversal.relative_confirmed = not item.reversal.relative_opposition
                if item.reversal.relative_opposition:
                    penalty = float(
                        regime_config.get("reversal_relative_opposition_penalty", 8.0)
                    )
                    item.trade_readiness = _clamp(item.trade_readiness - penalty)
                    item.confidence = _clamp(item.confidence - penalty * 0.55)
                    item.opportunity = _clamp(item.opportunity - penalty * 0.40)
                    if item.state in {"BUY", "SELL", "STRONG_LONG", "STRONG_SHORT"}:
                        item.state = "WATCH_LONG" if direction > 0 else "WATCH_SHORT"
                    item.reasons.append("W bleibt unsicher: relative Marktteilnahme zu schwach")

            if modifier <= warning_threshold:
                item.reasons.append(f"7/14/30D-Regime widerspricht ({modifier:+.1f})")
            elif modifier >= abs(warning_threshold):
                item.reasons.append(f"7/14/30D-Regime bestätigt ({modifier:+.1f})")

            # A strongly opposing multi-week regime may block a fresh E-entry,
            # but it never creates the opposite direction by itself.
            if (
                item.selected_setup == "EARLY"
                and modifier <= early_block
                and item.state in {"BUY", "SELL", "STRONG_LONG", "STRONG_SHORT"}
            ):
                item.state = "WATCH_LONG" if item.direction >= 0 else "WATCH_SHORT"
                item.reasons.append("frühes E durch Gegenregime blockiert")
            downgrade(item)
        return {symbol: result.to_dict() for symbol, result in results.items()}

    def _apply_extremity_context(
        self,
        signals: list[Signal],
        snapshots: Mapping[str, Mapping[str, Any]],
        daily_candles: Mapping[str, list[Mapping[str, Any]]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        hard_funding = float(self.config.get("funding_hard_hourly_pct", 0.05))

        def cap_immediate(item: Signal, *, to_watch: bool = False) -> None:
            if to_watch:
                if item.state in {"BUY", "STRONG_LONG"}:
                    item.state = "WATCH_LONG"
                elif item.state in {"SELL", "STRONG_SHORT"}:
                    item.state = "WATCH_SHORT"
            else:
                if item.state == "BUY":
                    item.state = "STRONG_LONG"
                elif item.state == "SELL":
                    item.state = "STRONG_SHORT"

        for item in signals:
            rows = list((snapshots.get(item.symbol) or {}).get("candles") or [])
            result = calculate_extremity(
                candles=rows,
                daily_candles=list(daily_candles.get(item.symbol) or []),
                current_price=item.price,
                current_timestamp_ms=item.candle_timestamp_ms,
                funding_hourly_pct=item.funding_hourly_pct,
                tape_quality=item.tape_quality,
                volume_confirmation=item.volume_confirmation,
                regime_score=item.regime_score if item.regime_available else None,
                funding_hard_hourly_pct=hard_funding,
            )
            item.extremity_available = result.available
            item.extremity_score = result.score
            item.extremity_confidence = result.confidence
            item.extremity_momentum = result.momentum
            item.extremity_vwap = result.vwap_deviation
            item.extremity_range = result.range_position
            item.extremity_funding = result.funding_crowding
            item.extremity_regime_adjustment = result.regime_adjustment
            item.extremity_intraday = result.intraday_score
            item.extremity_swing = result.swing_score
            item.extremity_swing_available = result.swing_available
            item.extremity_return_1d = result.return_1d
            item.extremity_return_3d = result.return_3d
            item.extremity_return_7d = result.return_7d

            if result.available and item.state not in {"INVALID_DATA", "NO_TRADE"}:
                direction = _direction(item.direction)
                chase = result.score * direction
                setup = _selected_setup(item)

                if item.selected_setup == "EARLY":
                    warning = float(self.config.get("early_chase_warning", 35.0))
                    block = float(self.config.get("early_chase_block", 50.0))
                    penalty = float(self.config.get("early_chase_penalty", 10.0))
                    window60 = item.windows.get(60)
                    aligned60 = bool(
                        window60 is not None
                        and window60.quality == "ok"
                        and window60.price_pct is not None
                        and _directional(window60.price_pct, direction)
                        >= max(0.03, item.noise_pct * 0.35)
                    )
                    exact_age = int(self.config.get("early_immediate_max_age_minutes", 1))
                    exact_used = float(self.config.get("early_immediate_max_consumed_fraction", 0.50))
                    has_broken = bool(setup.age_minutes is not None or setup.move_pct > 0)
                    stretch_chase = chase >= block or (chase >= warning and has_broken)
                    timing_chase = bool(
                        (setup.age_minutes is not None and setup.age_minutes > exact_age)
                        or setup.recovery_fraction > exact_used
                    )
                    item.chase_warning = bool(stretch_chase or timing_chase)
                    item.chase_blocked = bool(
                        chase >= block
                        or setup.recovery_fraction > float(self.config.get("early_max_consumed_fraction", 0.66))
                    )
                    if item.chase_warning:
                        applied = penalty if item.chase_blocked else penalty * 0.65
                        item.trade_readiness = _clamp(item.trade_readiness - applied)
                        item.confidence = _clamp(item.confidence - applied * 0.55)
                        item.opportunity = _clamp(item.opportunity - applied * 0.45)
                        item.state = "NO_TRADE"
                        item.reasons.append(
                            "E bereits gelaufen; WAIT statt Hinterherlaufen"
                            if has_broken else
                            "E zu stark überdehnt; WAIT"
                        )
                    elif item.state in {"BUY", "SELL"} and not aligned60 and chase >= warning * 0.75:
                        cap_immediate(item)
                        item.reasons.append("E NOW ohne 60m-Bestätigung begrenzt")

                elif item.selected_setup == "REVERSAL":
                    exhaustion = -result.score * direction
                    required = float(self.config.get("reversal_now_min_extremity", 18.0))
                    if item.state in {"BUY", "SELL"} and exhaustion < required:
                        cap_immediate(item)
                        item.reasons.append(
                            "W strukturell bestätigt, Extrempunkt aber nicht klar ausgereizt"
                        )
                else:
                    warning = float(self.config.get("extremity_chase_warning", 55.0))
                    block = float(self.config.get("extremity_chase_block", 72.0))
                    penalty = float(self.config.get("extremity_chase_penalty", 8.0))
                    if chase >= warning:
                        applied = penalty if chase >= block else penalty * 0.5
                        item.trade_readiness = _clamp(item.trade_readiness - applied)
                        item.confidence = _clamp(item.confidence - applied * 0.55)
                        item.opportunity = _clamp(item.opportunity - applied * 0.40)
                        cap_immediate(item, to_watch=chase >= block)
                        item.reasons.append(
                            f"Bewegung bereits gleichgerichtet überdehnt ({chase:.0f})"
                        )

            payload[item.symbol] = result.to_dict()
        return payload

    def _apply_swing_context(
        self,
        signals: list[Signal],
        snapshots: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Add live timing/activity plus BTC round-level pinning without creating direction."""
        payload: dict[str, Any] = {}
        for item in signals:
            rows = list((snapshots.get(item.symbol) or {}).get("candles") or [])
            result = calculate_swing_metrics(
                candles=rows,
                open_interest_usd=item.open_interest_usd,
                tape_quality=item.tape_quality,
                config=self.config,
            )
            item.swing_available = result.available
            item.swing_speed_pct = result.speed_pct_per_min
            item.swing_speed_bps = result.speed_bps
            item.swing_speed_score = result.speed_score
            item.swing_speed_ratio = result.speed_ratio
            item.swing_turnover_5m_pct = result.turnover_5m_pct
            item.swing_volume_pulse_ratio = result.volume_pulse_ratio
            item.live_activity_score = result.live_activity_score
            item.two_sided_score = result.two_sided_score
            row = result.to_dict()
            if item.symbol == "BTC":
                pin = calculate_pin(
                    candles=rows,
                    current_price=item.live_price or item.price,
                    noise_pct=item.noise_pct,
                    config=self.config,
                )
                item.btc_pin_available = pin.available
                item.btc_pin_level = pin.level
                item.btc_pin_score = pin.score
                row["pin"] = pin.to_dict()
            payload[item.symbol] = row
        return payload

    def _apply_springer_context(
        self,
        signals: list[Signal],
        snapshots: Mapping[str, Mapping[str, Any]],
        daily_candles: Mapping[str, list[Mapping[str, Any]]],
    ) -> dict[str, Any]:
        """Calculate direction-free recurring movement strength J00..J99."""
        payload: dict[str, Any] = {}
        class_map = {
            str(symbol).upper(): str(group).upper()
            for group, values in (self.config.get("springer_classes") or {}).items()
            for symbol in (values or [])
        }
        for item in signals:
            item.springer_class = class_map.get(item.symbol, "")
            result = calculate_springer_strength(
                minute_candles=list((snapshots.get(item.symbol) or {}).get("candles") or []),
                daily_candles=list(daily_candles.get(item.symbol) or []),
                config=self.config,
            )
            item.springer_available = result.available
            item.springer_score = result.score
            item.springer_reliability = result.reliability
            item.springer_daily_range_pct = result.daily_range_pct
            item.springer_intraday_impulse_pct = result.intraday_impulse_pct
            row = result.to_dict()
            row["class"] = item.springer_class
            payload[item.symbol] = row
        return payload

    def _apply_event_context(
        self,
        signals: list[Signal],
        event_marks: Mapping[str, Any] | None,
        event_display_codes: Mapping[str, str] | None = None,
    ) -> None:
        marks = event_marks if isinstance(event_marks, Mapping) else {}
        display_codes_provided = isinstance(event_display_codes, Mapping)
        display_codes = event_display_codes if display_codes_provided else {}

        def field(mark: Any, name: str, default: Any = None) -> Any:
            if mark is None:
                return default
            if isinstance(mark, Mapping):
                return mark.get(name, default)
            return getattr(mark, name, default)

        btc_mark = marks.get("BTC")
        btc_kind = str(field(btc_mark, "kind", "") or "")
        for item in signals:
            own_mark = marks.get(item.symbol)
            global_mark = (
                btc_mark
                if item.symbol != "BTC" and btc_kind in GLOBAL_BTC_EVENT_KINDS
                else None
            )
            applicable = [mark for mark in (own_mark, global_mark) if mark is not None]
            if not applicable:
                item.event_is_global = False
                item.event_display_code = str(display_codes.get(item.symbol, "") or "")
                continue

            # The coin-specific event owns the label/metadata. A confirmed BTC
            # macro event is additionally applied as market-wide risk without
            # duplicating its label on every altcoin.
            primary = own_mark or global_mark
            item.event_is_global = own_mark is None and global_mark is not None
            item.event_code = str(field(primary, "code", "") or "")
            display_default = item.event_code if own_mark is not None and not display_codes_provided else ""
            item.event_display_code = str(display_codes.get(item.symbol, display_default) or "")
            item.event_kind = str(field(primary, "kind", "") or "")
            item.event_title = str(field(primary, "title", "") or "")
            item.event_priority = max(
                _clamp(_f(field(mark, "priority", 0.0))) for mark in applicable
            )
            item.event_risk = max(
                _clamp(_f(field(mark, "risk", 0.0))) for mark in applicable
            )
            item.event_block_new = any(
                bool(field(mark, "block_new", False)) for mark in applicable
            )
            caps = [
                int(raw)
                for raw in (field(mark, "leverage_cap") for mark in applicable)
                if raw is not None
            ]
            item.event_leverage_cap = min(caps) if caps else None
            item.event_source_name = str(field(primary, "source_name", "") or "")
            item.event_source_url = str(field(primary, "source_url", "") or "")
            item.event_starts_at = field(primary, "starts_at")

            # Events never create a direction. They only reduce confidence or
            # block fresh risk close to a confirmed event.
            if item.event_risk >= 70:
                item.trade_readiness = _clamp(item.trade_readiness - 8.0)
                item.confidence = _clamp(item.confidence - 3.0)
            elif item.event_risk >= 45:
                item.trade_readiness = _clamp(item.trade_readiness - 4.0)
                item.confidence = _clamp(item.confidence - 2.0)

            active_network = any(
                str(field(mark, "kind", "") or "") == "NETWORK"
                and bool(field(mark, "active", False))
                for mark in applicable
            )
            active_emergency = any(
                str(field(mark, "kind", "") or "")
                in {"SECURITY", "MARKET_SHOCK"}
                and bool(field(mark, "active", False))
                for mark in applicable
            )
            reason_codes = []
            for mark in applicable:
                code = str(field(mark, "code", "") or "")
                if code and code not in reason_codes:
                    reason_codes.append(code)
            reason_code = "+".join(reason_codes) or "Ereignis"
            if active_network or active_emergency:
                item.state = "NO_TRADE"
                incident_reason = (
                    "aktive Netzwerkstörung"
                    if active_network
                    else "akutes Coin-Risiko"
                )
                item.reasons.append(f"{reason_code} {incident_reason}")
            elif item.event_block_new:
                if item.state == "BUY":
                    item.state = "STRONG_LONG"
                elif item.state == "SELL":
                    item.state = "STRONG_SHORT"
                elif item.state == "STRONG_LONG":
                    item.state = "WATCH_LONG"
                elif item.state == "STRONG_SHORT":
                    item.state = "WATCH_SHORT"
                item.reasons.append(f"{reason_code} blockiert neue Position kurzzeitig")
            else:
                item.reasons.append(f"kritisches Ereignis: {reason_code}")

    def _apply_event_source_health(
        self,
        signals: list[Signal],
        source_health: Mapping[str, Any] | None,
    ) -> None:
        """Attach per-symbol news/event coverage without inventing an E score."""
        health = source_health if isinstance(source_health, Mapping) else {}
        by_symbol = health.get("by_symbol") if isinstance(health.get("by_symbol"), Mapping) else {}
        minimum = float(self.config.get("event_source_min_coverage", 0.75))
        for item in signals:
            row = by_symbol.get(item.symbol) if isinstance(by_symbol, Mapping) else None
            if isinstance(row, Mapping):
                coverage = _clamp(_f(row.get("coverage"), 0.0), 0.0, 1.0)
            else:
                coverage = 0.0
            item.event_source_coverage = coverage
            # A currently verified mark has a known risk even if another source
            # is degraded. Otherwise zero risk is only meaningful with adequate
            # source coverage. Degraded coverage stays internal and is represented
            # compactly by E?? instead of a duplicate source warning.
            item.event_score_available = bool(item.event_code) or coverage >= minimum

    @staticmethod
    def _rank(signals: list[Signal]) -> list[Signal]:
        for item in signals:
            setup = _selected_setup(item)
            state_bonus = {
                "BUY": 16.0, "SELL": 16.0,
                "STRONG_LONG": 10.0, "STRONG_SHORT": 10.0,
                "WATCH_LONG": 4.0, "WATCH_SHORT": 4.0,
                "NO_TRADE": -8.0, "INVALID_DATA": -30.0,
            }.get(item.state, 0.0)
            late_penalty = 40.0 if setup.exit_hint or setup.phase in {"late", "invalidated"} else 0.0
            springer = float(item.springer_score) if item.springer_available else 50.0
            item.attention_score = _clamp(
                item.trade_readiness * 0.31
                + item.confidence * 0.17
                + item.opportunity * 0.18
                + setup.score * 0.17
                + item.tape_quality * 0.09
                + springer * 0.08
                + state_bonus
                - late_penalty
                - (float(5.0) if item.selected_setup == "TREND" else 0.0)
            )
        return sorted(
            signals,
            key=lambda item: (
                -STATE_TIER.get(item.state, 0),
                -item.attention_score,
                -item.trade_readiness,
                -item.confidence,
                item.alias,
            ),
        )

    def _summary_sort_key(self, item: Signal) -> tuple[float, float, float, float, str]:
        """Rank the neutral top-row radar; every active 15m shock outranks normal radar activity."""
        shock_bonus = 200.0 if item.event_kind == "MARKET_SHOCK" and item.event_block_new else 0.0
        return (
            shock_bonus + _radar_activity_score(item, self.config),
            abs(float(item.extremity_score)) if item.extremity_available else 0.0,
            float(item.springer_score) if item.springer_available else 0.0,
            float(item.live_activity_score),
            item.alias,
        )

    def _rotating_event_header_symbol(
        self,
        signals: list[Signal],
        now: datetime,
    ) -> str | None:
        candidates = [
            item for item in signals
            if item.symbol != "BTC" and _header_observation_event_code(item, self.config)
        ]
        if not candidates:
            return None
        ordered = sorted(
            candidates,
            key=lambda item: (
                float(item.event_priority + item.event_risk),
                float(item.event_risk),
                float(item.event_priority),
                float(item.attention_score),
                item.alias,
            ),
            reverse=True,
        )
        top_score = float(ordered[0].event_priority + ordered[0].event_risk)
        # Urgent/current events get the reserved slot first. Lower-priority news
        # still rotates once the urgent band clears instead of competing equally.
        urgent = [
            item for item in ordered
            if float(item.event_priority + item.event_risk) >= max(145.0, top_score - 8.0)
        ]
        pool = urgent if urgent else ordered
        minute_bucket = int(now.timestamp() // 60)
        return pool[minute_bucket % len(pool)].symbol

    def _summary_items(
        self,
        signals: list[Signal],
        priority_alt_symbol: str | None = None,
    ) -> list[Signal]:
        """Always show the three most active alts, then BTC; reserve one slot for important coin news."""
        requested = {str(value).upper() for value in self.config.get("candidate_symbols", [])}
        anchor_symbol = str(self.config.get("summary_anchor_symbol", "BTC")).upper()
        count = int(self.config.get("summary_coin_count", 4))
        anchor = next((item for item in signals if item.symbol == anchor_symbol), None)
        alt_slots = max(0, count - (1 if anchor is not None else 0))
        alternatives = [
            item for item in signals
            if item.symbol in requested and item.symbol != anchor_symbol
        ]
        priority_symbol = str(priority_alt_symbol or "").upper()
        priority_item = next((item for item in alternatives if item.symbol == priority_symbol), None)
        ranked = sorted(
            (item for item in alternatives if item is not priority_item),
            key=self._summary_sort_key,
            reverse=True,
        )
        selected = ranked[: max(0, alt_slots - (1 if priority_item is not None and alt_slots else 0))]
        if priority_item is not None and alt_slots:
            selected.append(priority_item)
        # Visual order is prospective strongest short on the left (OB/red),
        # neutral in the middle, prospective strongest long on the right (OS/green).
        selected = sorted(
            selected[:alt_slots],
            key=lambda item: (
                -float(item.extremity_score) if item.extremity_available else 0.0,
                -_radar_activity_score(item, self.config),
                item.alias,
            ),
        )
        if anchor is not None:
            selected.append(anchor)
        return selected[:count]

    def _format(
        self,
        signals: list[Signal],
        now: datetime,
        *,
        priority_header_symbol: str | None = None,
    ) -> str:
        ranked = self._rank(signals)
        summary = self._summary_items(signals, priority_header_symbol)
        anchor_symbol = str(self.config.get("summary_anchor_symbol", "BTC")).upper()
        event_codes = {
            item.symbol: (
                str(item.event_display_code or "")
                if item.symbol == anchor_symbol
                else _header_observation_event_code(item, self.config)
            )
            for item in summary
        }
        # The top row is a neutral early radar. Keep only true price-moving
        # event/incident labels there; execution/data-quality warnings remain
        # active in the signal engine and detail rows but do not consume header
        # width.
        warning_codes = {
            item.symbol: _header_observation_warnings(item, self.config)
            for item in summary
        }
        critical_warnings = {"SEC!", "NET!", "SHK!"}

        max_len = int(self.config.get("discord_max_codepoints_per_line", 42))
        max_columns = int(self.config.get("discord_max_display_columns_per_line", max_len + 4))
        header_max_len = int(self.config.get("discord_max_header_codepoints_per_line", max_len))
        header_max_columns = int(self.config.get("discord_max_header_display_columns_per_line", max_columns))

        def line_fits(value: str) -> bool:
            return len(value) <= max_len and _discord_display_columns(value) <= max_columns

        def header_fits(value: str) -> bool:
            return len(value) <= header_max_len and _discord_display_columns(value) <= header_max_columns

        def summary_line() -> str:
            tokens: list[str] = []
            for item in summary:
                event = event_codes.get(item.symbol, "")
                warnings = "".join(warning_codes.get(item.symbol, []))
                color = "⚫" if item.state == "INVALID_DATA" else extremity_color(item.extremity_score, item.extremity_available)
                if item.symbol == anchor_symbol:
                    pin = _pin_token(item)
                    payload = event or _btc_price_code(item.live_price or item.price)
                    tokens.append(f"{pin}{color}{payload}{warnings}")
                else:
                    tokens.append(f"{item.alias}{color}{event}{warnings}")
            return " ".join(tokens)

        header = summary_line()
        # Preserve events and acute warnings; ordinary market-quality warnings are
        # the first thing removed if Discord's compact top row would wrap.
        changed = True
        while not header_fits(header) and changed:
            changed = False
            for item in summary:
                codes = warning_codes.get(item.symbol, [])
                for index in range(len(codes) - 1, -1, -1):
                    if codes[index] in critical_warnings:
                        continue
                    del codes[index]
                    changed = True
                    header = summary_line()
                    break
                if header_fits(header):
                    break
        if not header_fits(header):
            # Keep BTC plus the reserved event/incident coin. Hide only duplicate
            # normal event labels on other radar slots before touching the slots.
            reserved = {anchor_symbol, str(priority_header_symbol or "").upper()}
            for item in summary:
                if item.symbol in reserved:
                    continue
                code = event_codes.get(item.symbol, "")
                if code and code not in critical_warnings:
                    event_codes[item.symbol] = ""
                    header = summary_line()
                    if header_fits(header):
                        break
        if not header_fits(header):
            # Broad data outages can repeat long DATA/STALE/GAP/BOOK/CND labels
            # across all radar slots. Keep BTC and the reserved event/incident
            # coin intact; redundant alt labels may be dropped exactly as in
            # the established compact header fallback.
            data_warnings = {"DATA!", "STALE!", "GAP!", "BOOK!", "CND!"}
            reserved = {anchor_symbol, str(priority_header_symbol or "").upper()}
            for item in summary:
                if item.symbol in reserved:
                    continue
                codes = warning_codes.get(item.symbol, [])
                for index in range(len(codes) - 1, -1, -1):
                    if codes[index] not in data_warnings:
                        continue
                    del codes[index]
                    header = summary_line()
                    break
                if header_fits(header):
                    break

        if not header_fits(header):
            # If several altcoins carry simultaneous critical labels, preserve
            # BTC and the currently reserved urgent coin. Other critical labels
            # remain active in risk logic and rotate into the reserved slot, but
            # duplicate header text may be suppressed to prevent wrapping.
            reserved = {anchor_symbol, str(priority_header_symbol or "").upper()}
            removable = sorted(
                (item for item in summary if item.symbol not in reserved),
                key=lambda item: (
                    float(item.event_priority + item.event_risk),
                    float(item.event_risk),
                    float(item.event_priority),
                    item.alias,
                ),
            )
            for item in removable:
                code = event_codes.get(item.symbol, "")
                if code in critical_warnings:
                    event_codes[item.symbol] = ""
                warning_codes[item.symbol] = [
                    value for value in warning_codes.get(item.symbol, [])
                    if value not in critical_warnings
                ]
                header = summary_line()
                if header_fits(header):
                    break

        if not header_fits(header):
            raise RuntimeError("Discord-Top-Zeilenlimit überschritten")

        self.last_header_event_symbols = tuple(item.symbol for item in summary if event_codes.get(item.symbol, ""))
        lines = [header]

        btc = next((item for item in ranked if item.symbol == "BTC"), None)
        maximum_details = int(self.config.get("maximum_detail_count", 4))
        timing_min = float(self.config.get("detail_timing_confirmation_min_score", 30))

        def action_quality(item: Signal) -> tuple[float, float, float, float, str]:
            return (
                float(STATE_TIER.get(item.state, 0)),
                float(item.attention_score),
                float(item.trade_readiness),
                _timing_confirmation_score(item),
                item.alias,
            )

        mandatory: list[Signal] = []
        optional: list[Signal] = []
        for item in ranked:
            if item.symbol == "BTC" or item.state == "INVALID_DATA":
                continue
            if item.event_kind == "MARKET_SHOCK" and item.event_block_new:
                continue
            action = _action_code(item)
            setup = _selected_setup(item)
            valid_setup = setup.phase in {"ready", "strong", "forming"} and not setup.exit_hint
            timing = _timing_confirmation_score(item)
            if action in {"TRY", "NOW"}:
                mandatory.append(item)
            elif action == "NEAR" and valid_setup:
                # A genuine NEAR is already actionable as a small scout.
                # Live timing confirms/ranks it but never hides it from the
                # limited lower slots.
                mandatory.append(item)
            elif (
                valid_setup
                and STATE_TIER.get(item.state, 0) >= 2
                and item.attention_score >= float(self.config.get("detail_attention_threshold", 68))
                and (not item.swing_available or timing >= timing_min)
            ):
                optional.append(item)

        # Action-tier-first selection with live timing as an extra tie-breaker.
        btc_detail_allowed = btc is not None and not (btc.event_kind == "MARKET_SHOCK" and btc.event_block_new)
        alt_slots = max(0, maximum_details - (1 if btc_detail_allowed else 0))
        chosen: list[Signal] = []
        seen: set[str] = set()
        for item in sorted(mandatory, key=action_quality, reverse=True):
            if item.symbol not in seen and len(chosen) < alt_slots:
                chosen.append(item); seen.add(item.symbol)
        for item in sorted(optional, key=action_quality, reverse=True):
            if item.symbol not in seen and len(chosen) < alt_slots:
                chosen.append(item); seen.add(item.symbol)
        chosen = sorted(
            chosen,
            key=lambda item: (
                0 if _direction(item.direction) < 0 else (1 if _direction(item.direction) == 0 else 2),
                -float(item.attention_score),
                -float(item.trade_readiness),
                -float(item.confidence),
                item.alias,
            ),
        )

        def detail_line(item: Signal) -> str:
            if item.state == "INVALID_DATA":
                return f"⚫? 05⚫20⚫60⚫ J?? E?? {item.alias}?00"
            windows = "".join(
                f"{minutes:02d}{_window_color(item.windows.get(minutes, Window(minutes)))}"
                for minutes in DISPLAY_WINDOWS
            )
            core = (
                f"{_detail_head(item)} {windows} {_springer_token(item)} "
                f"{_event_risk_token(item, self.config)} {_signed_extremity_token(item)}"
            )
            event = "" if item.symbol == anchor_symbol else str(item.event_display_code or "")
            warnings = [code for code in _warning_codes(item, self.config) if not event or code not in event]
            extras = ([event] if event else []) + warnings
            line = core if not extras else f"{core} {' '.join(extras)}"
            # Special information is intentionally rightmost and expendable by
            # priority; the fixed professional core never changes alignment.
            while not line_fits(line) and extras:
                removable = next((i for i in range(len(extras) - 1, -1, -1) if extras[i] not in critical_warnings and extras[i] != event), None)
                if removable is None:
                    break
                extras.pop(removable)
                line = core if not extras else f"{core} {' '.join(extras)}"
            if not line_fits(line) and event and event not in critical_warnings:
                extras = [value for value in extras if value != event]
                line = core if not extras else f"{core} {' '.join(extras)}"
            if not line_fits(line):
                raise RuntimeError(f"Discord-Detailzeilenlimit überschritten: {item.symbol}")
            return line

        if btc_detail_allowed and btc is not None:
            lines.append(detail_line(btc))
        for item in chosen:
            lines.append(detail_line(item))
        if btc is None and not chosen and ranked:
            lines.append(detail_line(ranked[0]))

        return "\n".join(lines)

    def run(
        self,
        *,
        event_marks: Mapping[str, Any] | None = None,
        event_display_codes: Mapping[str, str] | None = None,
        event_source_health: Mapping[str, Any] | None = None,
        incident_state_path: Path | None = None,
        signal_transition_state_path: Path | None = None,
        signal_streak_state_path: Path | None = None,
        signal_evaluation_state_path: Path | None = None,
        daily_candle_cache_path: Path | None = None,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        self.generated_at = now
        allowed = {str(value).upper() for value in self.config.get("candidate_symbols", [])}
        markets = {
            str(row.get("symbol")).upper(): row
            for row in self.client.markets()
            if str(row.get("symbol")).upper() in allowed
            and str(row.get("status")).lower() == "active"
            and str(row.get("market_type")).lower() == "perp"
        }
        try:
            raw_funding = self.client.funding()
        except Exception:
            raw_funding = []
        funding: dict[str, float] = {}
        for row in raw_funding:
            symbol = str(row.get("symbol")).upper()
            if symbol not in allowed or str(row.get("exchange")).lower() != "lighter":
                continue
            try:
                rate = float(row.get("rate"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(rate):
                funding[symbol] = rate

        signals: list[Signal] = []
        snapshots: dict[str, dict[str, Any]] = {}
        daily_candles: dict[str, list[Mapping[str, Any]]] = {}
        fresh_daily, fallback_daily, daily_timestamps = _load_daily_candle_cache(
            daily_candle_cache_path,
            now=now,
            allowed=allowed,
            refresh_minutes=max(5, int(self.config.get("daily_candle_cache_refresh_minutes", 30))),
            max_stale_hours=max(1, int(self.config.get("daily_candle_cache_max_stale_hours", 6))),
        )
        refreshed_daily: set[str] = set()
        daily_batch = max(1, int(self.config.get("daily_candle_refresh_batch_size", 4)))
        def daily_priority(symbol: str) -> tuple[int, str]:
            # Missing cache first, then the oldest known timestamp. This keeps
            # cold-start bounded while guaranteeing every pool coin refreshes.
            if symbol not in fallback_daily:
                return (0, "")
            return (1, str(daily_timestamps.get(symbol, "")))
        refresh_daily_symbols = set(
            sorted((symbol for symbol in markets if symbol not in fresh_daily), key=daily_priority)[:daily_batch]
        )
        workers = min(8, max(1, int(self.config.get("parallel_requests", 6))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self._load_one,
                    row,
                    fresh_daily.get(symbol),
                    fallback_daily.get(symbol),
                    symbol in refresh_daily_symbols,
                ): symbol
                for symbol, row in markets.items()
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    candles, book, daily, daily_refreshed = future.result()
                    if daily_refreshed:
                        refreshed_daily.add(symbol)
                    snapshots[symbol] = {
                        "market": markets[symbol],
                        "candles": candles,
                        "book": book,
                    }
                    daily_candles[symbol] = daily
                    signals.append(
                        self._analyse(
                            markets[symbol],
                            funding.get(symbol),
                            candles,
                            book,
                        )
                    )
                except Exception as exc:
                    aliases = self.config.get("aliases") or {}
                    signals.append(
                        Signal(
                            symbol=symbol,
                            alias=str(aliases.get(symbol, symbol[:3])).upper(),
                            reasons=[f"{type(exc).__name__}: {exc}"],
                        )
                    )
        for symbol in sorted(allowed - set(markets)):
            signals.append(
                Signal(
                    symbol,
                    str((self.config.get("aliases") or {}).get(symbol, symbol[:3])).upper(),
                    reasons=["kein aktiver Lighter-Krypto-Perp"],
                )
            )

        _write_daily_candle_cache(
            daily_candle_cache_path,
            now=now,
            allowed=allowed,
            rows_by_symbol={**fallback_daily, **daily_candles},
            refreshed_symbols=refreshed_daily,
            old_timestamps=daily_timestamps,
        )

        self._apply_btc_context(signals)
        regime_payload = self._apply_regime_context(signals, snapshots, daily_candles, now)
        extremity_payload = self._apply_extremity_context(signals, snapshots, daily_candles)
        swing_payload = self._apply_swing_context(signals, snapshots)
        springer_payload = self._apply_springer_context(signals, snapshots, daily_candles)
        incident_snapshot = detect_spontaneous_incidents(
            self.config,
            signals=signals,
            snapshots=snapshots,
            event_marks=event_marks,
            now=now,
            state_path=incident_state_path,
        )
        merged_marks = dict(event_marks or {})
        merged_marks.update(incident_snapshot.marks)
        if event_display_codes is None:
            merged_display_codes: dict[str, str] = {
                str(symbol): str(
                    mark.get("code", "")
                    if isinstance(mark, Mapping)
                    else getattr(mark, "code", "")
                )
                for symbol, mark in merged_marks.items()
            }
        else:
            merged_display_codes = {
                str(symbol): str(code)
                for symbol, code in event_display_codes.items()
            }
        merged_display_codes.update(incident_snapshot.display_codes)
        self._apply_event_context(signals, merged_marks, merged_display_codes)
        self._apply_event_source_health(signals, event_source_health)
        watch_threshold = float(self.config.get("watch_trade_readiness", 51))
        strong_threshold = float(self.config.get("strong_trade_readiness", 58))
        immediate_threshold = float(self.config.get("immediate_trade_readiness", 69))
        # Normal project events share the reserved header slot instead of
        # permanently hiding one another. Acute incidents still override this
        # choice through incident_snapshot.header_symbol below.
        event_header_symbol = self._rotating_event_header_symbol(signals, now)
        transition_payload: dict[str, Any] = {}
        if signal_transition_state_path is not None:
            transition_payload = apply_signal_transition_guard(
                signals,
                state_path=signal_transition_state_path,
                now=now,
                config=self.config,
                action_getter=_action_code,
            )
        # Record the final gap between the numeric readiness tier and the
        # actually permitted action only after every context/incident/flip guard
        # has had a chance to downgrade the state. This is critical for later
        # threshold replay: a high score that was safety-limited must not look
        # like an ordinary threshold miss.
        for item in signals:
            expected_tier = (
                3 if item.trade_readiness >= immediate_threshold else
                2 if item.trade_readiness >= strong_threshold else
                1 if item.trade_readiness >= watch_threshold else 0
            )
            actual_tier = {"NOW": 3, "TRY": 2, "NEAR": 1}.get(_action_code(item), 0)
            item.state_limited_by_guard = bool(actual_tier < expected_tier)

        if signal_streak_state_path is not None:
            apply_signal_streaks(
                signals,
                state_path=signal_streak_state_path,
                now=now,
                action_getter=_action_code,
            )
        else:
            for signal in signals:
                action = _action_code(signal)
                if action in {"NEAR", "TRY", "NOW"}:
                    signal.action_streak_count = 1
                    signal.action_streak_action = action
                    signal.action_streak_direction = _direction(signal.direction)
        evaluation_payload: dict[str, Any] = {}
        if signal_evaluation_state_path is not None:
            evaluation_payload = update_signal_evaluation(
                signals,
                state_path=signal_evaluation_state_path,
                now=now,
                action_getter=_action_code,
                config=self.config,
            )
        self.last_incidents = incident_snapshot
        self.last_signals = self._rank(signals)
        self.last_snapshots = snapshots
        report = self._format(
            signals,
            now,
            priority_header_symbol=incident_snapshot.header_symbol or event_header_symbol,
        )
        acute_shock = any(
            item.event_kind == "MARKET_SHOCK" and item.event_block_new
            for item in signals
        )
        minimum_lines = 1 if acute_shock else int(self.config.get("minimum_detail_count", 1)) + 1
        maximum_lines = int(self.config.get("maximum_detail_count", 4)) + 1
        if not minimum_lines <= len(report.splitlines()) <= maximum_lines:
            raise RuntimeError("Discord-Ausgabe hat unerwartete Zeilenzahl")
        payload = {
            "version": APP_VERSION,
            "package_revision": PACKAGE_REVISION,
            "generated_at": now.isoformat(),
            "report": report,
            "signals": [asdict(item) for item in self.last_signals],
            "regime": regime_payload,
            "extremity": extremity_payload,
            "swing": swing_payload,
            "springer": springer_payload,
            "signal_transition": transition_payload,
            "signal_evaluation": evaluation_payload,
            "incidents": incident_snapshot.to_dict(),
            "event_marks": {
                symbol: (asdict(mark) if hasattr(mark, "__dataclass_fields__") else dict(mark))
                for symbol, mark in merged_marks.items()
            },
        }
        return report, payload

    def _load_one(
        self,
        market: Mapping[str, Any],
        fresh_daily: list[Mapping[str, Any]] | None = None,
        fallback_daily: list[Mapping[str, Any]] | None = None,
        refresh_daily: bool = True,
    ) -> tuple[list[Mapping[str, Any]], Mapping[str, Any], list[Mapping[str, Any]], bool]:
        market_id = int(market["market_id"])
        candle_count = int(self.config.get("candle_count", 360))
        daily_count = int((self.config.get("regime") or {}).get("daily_candle_count", 40))
        candles = self.client.candles(market_id, count=candle_count)
        book = self.client.book(market_id)
        if fresh_daily:
            return candles, book, fresh_daily[-daily_count:], False
        if not refresh_daily:
            return candles, book, (fallback_daily or [])[-daily_count:], False
        try:
            daily = self.client.daily_candles(market_id, count=daily_count)
            return candles, book, daily, bool(daily)
        except Exception:
            daily = (fallback_daily or [])[-daily_count:]
            return candles, book, daily, False


