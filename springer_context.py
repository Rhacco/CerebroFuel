# r2
"""Persistent direction-free springer strength J00..J99 for CF v7.1.0.

J measures how often and how quickly a market makes meaningful 15-minute moves
under normal conditions. It combines real non-overlapping 15m impulses retained
for up to 30 days with the current intraday tape and robust daily ranges. A
single exceptional move is discounted; acute shocks and external events remain
separate SHK!/E signals.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

HISTORY_SCHEMA = "springer-history-v710-r2"
COMPATIBLE_HISTORY_SCHEMAS = {HISTORY_SCHEMA, "springer-history-v710-r1", "springer-history-v700-r1"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _ts(row: Mapping[str, Any]) -> int:
    raw = int(_f(row.get("t"), _f(row.get("timestamp"), _f(row.get("time")))))
    return raw * 1000 if 0 < raw < 10_000_000_000 else raw


def _clean(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_time: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        stamp = _ts(row)
        close = _f(row.get("c"))
        if stamp > 0 and close > 0:
            by_time[stamp] = row
    return [by_time[key] for key in sorted(by_time)]


def _latest_contiguous_minutes(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    clean = _clean(rows)
    if not clean:
        return []
    start = len(clean) - 1
    while start > 0 and _ts(clean[start]) - _ts(clean[start - 1]) == 60_000:
        start -= 1
    return clean[start:]


def _daily_contiguous(rows: Sequence[Mapping[str, Any]]) -> bool:
    clean = list(rows)
    return bool(clean) and all(
        _ts(right) - _ts(left) == 86_400_000 for left, right in zip(clean, clean[1:])
    )


def _pct(start: float, end: float) -> float:
    return (end / start - 1.0) * 100.0 if start > 0 and end > 0 else 0.0


def _daily_range(row: Mapping[str, Any]) -> float:
    high = _f(row.get("h"))
    low = _f(row.get("l"))
    close = _f(row.get("c"))
    return (high - low) / close * 100.0 if close > 0 and high >= low > 0 else 0.0


def _quantile(values: Sequence[float], q: float) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pos = max(0.0, min(1.0, q)) * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temp.replace(path)


def _load_history_payload(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    if raw.get("schema") not in COMPATIBLE_HISTORY_SCHEMAS or not isinstance(raw.get("symbols"), Mapping):
        return {}
    return dict(raw)


def _history_from_payload(raw: Mapping[str, Any]) -> dict[str, list[list[float]]]:
    rows_by_symbol = raw.get("symbols") if isinstance(raw.get("symbols"), Mapping) else {}
    result: dict[str, list[list[float]]] = {}
    for symbol, rows in rows_by_symbol.items():
        if not isinstance(rows, list):
            continue
        clean: dict[int, float] = {}
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                continue
            stamp = int(_f(row[0]))
            move = _f(row[1], float("nan"))
            if stamp > 0 and math.isfinite(move):
                clean[stamp] = move
        if clean:
            result[str(symbol).upper()] = [[stamp, clean[stamp]] for stamp in sorted(clean)]
    return result



def _extract_aligned_impulses(
    rows: Sequence[Mapping[str, Any]],
    *,
    window_minutes: int,
    alignment_minutes: int,
) -> list[list[float]]:
    """Return closed, non-overlapping/aligned 15m moves as [end_ms, signed_pct]."""
    clean = _clean(rows)
    closes = {_ts(row): _f(row.get("c")) for row in clean}
    window_ms = max(1, window_minutes) * 60_000
    alignment_ms = max(window_ms, max(1, alignment_minutes) * 60_000)
    result: list[list[float]] = []
    for end_ms in sorted(closes):
        if end_ms % alignment_ms != 0:
            continue
        start_ms = end_ms - window_ms
        start = closes.get(start_ms)
        end = closes.get(end_ms)
        if start and end:
            result.append([end_ms, _pct(start, end)])
    return result


def _extract_native_15m_impulses(
    rows: Sequence[Mapping[str, Any]],
    *,
    window_minutes: int,
) -> list[list[float]]:
    """Return one signed move per closed native history candle.

    The monitor requests exactly the configured 15m resolution with timestamps
    placed at candle end. Open-to-close therefore represents the same
    non-overlapping interval used by the persistent J history.
    """
    clean = _clean(rows)
    window_ms = max(1, window_minutes) * 60_000
    result: list[list[float]] = []
    previous_stamp = 0
    for row in clean:
        stamp = _ts(row)
        open_price = _f(row.get("o"))
        close_price = _f(row.get("c"))
        if stamp <= 0 or stamp % window_ms != 0 or open_price <= 0 or close_price <= 0:
            continue
        # Reject malformed/overlapping native history instead of silently
        # treating an arbitrary cadence as a 15m observation. Gaps are allowed
        # because later metrics explicitly expose coverage.
        if previous_stamp and stamp - previous_stamp < window_ms:
            continue
        previous_stamp = stamp
        result.append([stamp, _pct(open_price, close_price)])
    return result


def springer_backfill_requests(
    *,
    path: Path | None,
    now: datetime,
    allowed_symbols: set[str],
    config: Mapping[str, Any],
    retry_minutes: int = 30,
) -> list[tuple[str, int | None]]:
    """Plan oldest-first native 15m history chunks without exceeding caller budget.

    Each request can seed at most 500 Lighter candles, so a genuine multi-week
    history is filled progressively over normal monitor runs. Symbols with the
    least retained span are served first. A failed/empty request gets a cooldown
    so one young or temporarily unavailable market cannot starve the rest.
    """
    raw = _load_history_payload(path)
    history = _history_from_payload(raw)
    attempts = raw.get("backfill_attempted_at") if isinstance(raw.get("backfill_attempted_at"), Mapping) else {}
    now_utc = now.astimezone(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    min_days = max(1.0, float(config.get("springer_history_min_days", 2)))
    retention_days = max(7, min(45, int(config.get("springer_history_days", 30))))
    target_span_days = max(min_days, float(retention_days) - 1.0)
    cutoff_ms = int((now_utc - timedelta(days=retention_days)).timestamp() * 1000)
    retry_seconds = max(5, int(retry_minutes)) * 60

    candidates: list[tuple[float, int, str, int | None]] = []
    for symbol in sorted({str(value).upper() for value in allowed_symbols}):
        rows = [
            (int(_f(row[0])), _f(row[1]))
            for row in history.get(symbol, [])
            if len(row) == 2 and cutoff_ms <= int(_f(row[0])) <= now_ms
        ]
        span_days = (rows[-1][0] - rows[0][0]) / 86_400_000 if len(rows) >= 2 else 0.0
        if span_days >= target_span_days:
            continue
        attempted = _parse_history_iso(attempts.get(symbol))
        if attempted is not None and (now_utc - attempted).total_seconds() < retry_seconds:
            continue
        # End immediately before the oldest retained 15m boundary. With no
        # history yet, None asks the client for the latest closed native chunk.
        end_timestamp = max(0, rows[0][0] // 1000 - 1) if rows else None
        candidates.append((span_days, len(rows), symbol, end_timestamp))
    return [(symbol, end_timestamp) for _, _, symbol, end_timestamp in sorted(candidates)]



def _parse_history_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def update_springer_history(
    *,
    path: Path | None,
    minute_candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    now: datetime,
    allowed_symbols: set[str],
    config: Mapping[str, Any],
    backfill_candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    backfill_failed_symbols: set[str] | None = None,
) -> dict[str, list[list[float]]]:
    """Merge live minute windows plus any rate-budgeted native 15m bootstrap.

    Historical candles are supplied by the caller only when spare Lighter
    request budget exists. After that seed, normal monitor runs extend the same
    compact 7/30-day history without recurring bootstrap calls.
    """
    raw_payload = _load_history_payload(path)
    history = _history_from_payload(raw_payload)
    backfill_rows = backfill_candles_by_symbol if isinstance(backfill_candles_by_symbol, Mapping) else {}
    retention_days = max(7, min(45, int(config.get("springer_history_days", 30))))
    window = max(5, int(config.get("springer_history_window_minutes", 15)))
    alignment = max(window, int(config.get("springer_history_alignment_minutes", window)))
    cutoff_ms = int((now.astimezone(timezone.utc) - timedelta(days=retention_days)).timestamp() * 1000)
    now_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)

    merged: dict[str, list[list[float]]] = {}
    for symbol in sorted(allowed_symbols):
        old_rows = history.get(symbol, [])
        values: dict[int, float] = {
            int(row[0]): float(row[1])
            for row in old_rows
            if len(row) == 2 and cutoff_ms <= int(_f(row[0])) <= now_ms
        }
        for stamp, move in _extract_native_15m_impulses(
            list(backfill_rows.get(symbol) or []),
            window_minutes=window,
        ):
            if cutoff_ms <= int(stamp) <= now_ms:
                values[int(stamp)] = float(move)
        for stamp, move in _extract_aligned_impulses(
            list(minute_candles_by_symbol.get(symbol) or []),
            window_minutes=window,
            alignment_minutes=alignment,
        ):
            if cutoff_ms <= int(stamp) <= now_ms:
                # Live 1m data is the freshest source for a duplicate boundary.
                values[int(stamp)] = float(move)
        if values:
            merged[symbol] = [[stamp, round(values[stamp], 8)] for stamp in sorted(values)]

    if path is not None:
        attempts = raw_payload.get("backfill_attempted_at") if isinstance(raw_payload.get("backfill_attempted_at"), Mapping) else {}
        attempts = {str(key).upper(): str(value) for key, value in attempts.items()}
        attempted_at = now.astimezone(timezone.utc).isoformat()
        for symbol in backfill_rows:
            attempts.pop(str(symbol).upper(), None)
        for symbol in backfill_failed_symbols or set():
            if str(symbol).upper() in allowed_symbols:
                attempts[str(symbol).upper()] = attempted_at
        _atomic_json(path, {
            "schema": HISTORY_SCHEMA,
            "updated_at": attempted_at,
            "retention_days": retention_days,
            "backfill_attempted_at": attempts,
            "symbols": merged,
        })
    return merged


def _history_rows(
    rows: Sequence[Sequence[float]],
    *,
    now: datetime,
    days: int,
) -> list[tuple[int, float]]:
    cutoff = int((now.astimezone(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    now_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
    result: list[tuple[int, float]] = []
    for row in rows:
        if len(row) != 2:
            continue
        stamp = int(_f(row[0]))
        move = _f(row[1], float("nan"))
        if cutoff <= stamp <= now_ms and math.isfinite(move):
            result.append((stamp, move))
    return sorted(result)


def _historical_metrics(rows: Sequence[tuple[int, float]], expected_per_day: int) -> dict[str, float]:
    if not rows:
        return {
            "typical": 0.0, "frequency": 0.0, "strong_frequency": 0.0,
            "concentration": 1.0, "direction_balance": 0.0,
            "active_day_fraction": 0.0, "coverage": 0.0, "span_days": 0.0,
        }
    absolute = [abs(move) for _, move in rows]
    typical = 0.55 * statistics.median(absolute) + 0.45 * _quantile(absolute, 0.75)
    meaningful_floor = 0.15
    strong_floor = 0.30
    frequency = sum(value >= meaningful_floor for value in absolute) / len(absolute)
    strong_frequency = sum(value >= strong_floor for value in absolute) / len(absolute)
    total = sum(absolute)
    concentration = max(absolute) / total if total > 0 else 1.0
    meaningful_signed = [move for _, move in rows if abs(move) >= meaningful_floor]
    if meaningful_signed:
        up = sum(move > 0 for move in meaningful_signed)
        down = sum(move < 0 for move in meaningful_signed)
        direction_balance = 2.0 * min(up, down) / max(1, up + down)
    else:
        direction_balance = 0.0

    by_day: dict[int, list[float]] = {}
    for stamp, move in rows:
        by_day.setdefault(stamp // 86_400_000, []).append(abs(move))
    active_days = sum(sum(value >= meaningful_floor for value in values) >= 4 for values in by_day.values())
    active_day_fraction = active_days / max(1, len(by_day))
    first, last = rows[0][0], rows[-1][0]
    span_days = max(0.0, (last - first) / 86_400_000)
    expected = max(1.0, (span_days + 1.0) * expected_per_day)
    coverage = _clamp(len(rows) / expected, 0.0, 1.0)
    return {
        "typical": typical,
        "frequency": frequency,
        "strong_frequency": strong_frequency,
        "concentration": concentration,
        "direction_balance": direction_balance,
        "active_day_fraction": active_day_fraction,
        "coverage": coverage,
        "span_days": span_days,
    }


@dataclass(frozen=True)
class SpringerResult:
    available: bool = False
    score: float = 0.0
    daily_range_pct: float = 0.0
    recent_daily_range_pct: float = 0.0
    intraday_impulse_pct: float = 0.0
    impulse_frequency: float = 0.0
    reliability: float = 0.0
    concentration: float = 0.0
    minute_coverage: float = 0.0
    daily_samples: int = 0
    minute_samples: int = 0
    history_samples: int = 0
    history_days: float = 0.0
    history_coverage: float = 0.0
    history_impulse_pct_7d: float = 0.0
    history_impulse_pct_30d: float = 0.0
    history_frequency_7d: float = 0.0
    history_frequency_30d: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_springer_strength(
    *,
    minute_candles: Sequence[Mapping[str, Any]],
    daily_candles: Sequence[Mapping[str, Any]],
    historical_impulses: Sequence[Sequence[float]] = (),
    now: datetime | None = None,
    config: Mapping[str, Any],
) -> SpringerResult:
    if not bool(config.get("springer_enabled", True)):
        return SpringerResult(reason="disabled")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    minute_need = max(120, int(config.get("springer_minute_lookback_minutes", 300)))
    minute_min = max(90, int(config.get("springer_min_contiguous_minutes", 180)))
    minute_rows = _latest_contiguous_minutes(minute_candles)[-(minute_need + 1):]
    if len(minute_rows) < minute_min + 1:
        return SpringerResult(
            minute_samples=len(minute_rows),
            reason=f"insufficient contiguous minute history {len(minute_rows)}/{minute_min + 1}",
        )

    daily_rows = _clean(daily_candles)
    daily_need = max(10, int(config.get("springer_daily_lookback_days", 30)))
    daily_rows = daily_rows[-daily_need:]
    if len(daily_rows) < 10 or not _daily_contiguous(daily_rows):
        return SpringerResult(
            minute_samples=len(minute_rows), daily_samples=len(daily_rows),
            reason="insufficient contiguous daily history",
        )

    ranges = [value for value in (_daily_range(row) for row in daily_rows) if value > 0]
    if len(ranges) < 10:
        return SpringerResult(
            minute_samples=len(minute_rows), daily_samples=len(ranges),
            reason="invalid daily range history",
        )

    typical = statistics.median(ranges)
    recent = statistics.median(ranges[-min(7, len(ranges)):])
    mad = statistics.median(abs(value - typical) for value in ranges)
    daily_consistency = _clamp(1.0 - mad / max(typical, 1e-9), 0.0, 1.0)
    active_days = sum(value >= max(1.50, 0.70 * typical) for value in ranges) / len(ranges)
    recency_ratio = _clamp(recent / max(typical, 1e-9), 0.50, 1.75)
    daily_strength = 99.0 * (1.0 - math.exp(-max(0.0, typical) / 4.0))
    daily_strength *= 0.85 + 0.15 * _clamp((recency_ratio - 0.50) / 1.25, 0.0, 1.0)

    closes = [_f(row.get("c")) for row in minute_rows]
    impulses: list[float] = []
    signed: list[float] = []
    for end in range(15, len(closes), 5):
        move = _pct(closes[end - 15], closes[end])
        impulses.append(abs(move))
        signed.append(move)
    if len(impulses) < 20:
        return SpringerResult(
            minute_samples=len(minute_rows), daily_samples=len(ranges),
            reason="insufficient 15m impulse observations",
        )

    impulse_median = statistics.median(impulses)
    impulse_q75 = _quantile(impulses, 0.75)
    impulse_typical = 0.55 * impulse_median + 0.45 * impulse_q75
    one_minute = [abs(_pct(a, b)) for a, b in zip(closes, closes[1:])]
    minute_med = statistics.median(one_minute) if one_minute else 0.0
    minute_mad = statistics.median(abs(value - minute_med) for value in one_minute) if one_minute else 0.0
    robust_minute = max(0.005, minute_med + 1.4826 * minute_mad)
    meaningful_threshold = max(0.12, min(0.45, robust_minute * math.sqrt(15.0) * 0.90))
    meaningful = [value for value in impulses if value >= meaningful_threshold]
    frequency = len(meaningful) / len(impulses)
    impulse_strength = 99.0 * (1.0 - math.exp(-impulse_typical / 0.45))
    frequency_score = _clamp(frequency / 0.50 * 99.0)

    total_impulse = sum(impulses)
    concentration = max(impulses) / total_impulse if total_impulse > 0 else 1.0
    current_concentration_penalty = _clamp((concentration - 0.18) / 0.32, 0.0, 1.0)
    meaningful_signed = [move for move in signed if abs(move) >= meaningful_threshold]
    if meaningful_signed:
        up = sum(value > 0 for value in meaningful_signed)
        down = sum(value < 0 for value in meaningful_signed)
        direction_balance = 2.0 * min(up, down) / max(1, up + down)
    else:
        direction_balance = 0.0
    current_consistency = _clamp(
        0.62 * min(1.0, frequency / 0.35)
        + 0.15 * direction_balance
        + 0.23 * (1.0 - current_concentration_penalty),
        0.0,
        1.0,
    )
    minute_coverage = _clamp(len(minute_rows) / max(1, minute_need + 1), 0.0, 1.0)

    window_minutes = max(5, int(config.get("springer_history_window_minutes", 15)))
    expected_per_day = max(1, 1440 // window_minutes)
    hist_7 = _history_rows(historical_impulses, now=now, days=7)
    hist_30 = _history_rows(
        historical_impulses,
        now=now,
        days=max(7, int(config.get("springer_history_days", 30))),
    )
    h7 = _historical_metrics(hist_7, expected_per_day)
    h30 = _historical_metrics(hist_30, expected_per_day)
    history_days = h30["span_days"]
    history_samples = len(hist_30)
    min_history_days = max(1.0, float(config.get("springer_history_min_days", 2)))
    min_history_samples = max(8, int(config.get("springer_history_min_samples", 24)))
    history_maturity = _clamp(
        min(
            1.0,
            history_days / min_history_days,
            history_samples / min_history_samples,
        )
        * (0.55 + 0.45 * h30["coverage"]),
        0.0,
        1.0,
    )

    # Use 7d more strongly once available, but keep 30d as the anti-one-off
    # baseline. Both are made of actual 15m windows accumulated by normal runs.
    hist_typical = 0.62 * h7["typical"] + 0.38 * h30["typical"] if hist_7 else h30["typical"]
    hist_frequency = 0.62 * h7["frequency"] + 0.38 * h30["frequency"] if hist_7 else h30["frequency"]
    hist_strong_frequency = 0.62 * h7["strong_frequency"] + 0.38 * h30["strong_frequency"] if hist_7 else h30["strong_frequency"]
    history_speed_score = 99.0 * (1.0 - math.exp(-max(0.0, hist_typical) / 0.45))
    history_frequency_score = _clamp((0.72 * hist_frequency / 0.50 + 0.28 * hist_strong_frequency / 0.25) * 99.0)
    history_strength = 0.62 * history_speed_score + 0.38 * history_frequency_score
    history_concentration_penalty = _clamp((h30["concentration"] - 0.035) / 0.115, 0.0, 1.0)
    history_consistency = _clamp(
        0.46 * h30["active_day_fraction"]
        + 0.24 * h30["direction_balance"]
        + 0.30 * (1.0 - history_concentration_penalty),
        0.0,
        1.0,
    )

    # Reliability is separate from raw movement size. Persistent real 15m
    # history progressively replaces the cold-start proxy instead of changing J
    # discontinuously after the first day.
    cold_reliability = _clamp(
        (0.38 * daily_consistency + 0.28 * active_days + 0.34 * current_consistency)
        * 100.0
        * (0.75 + 0.25 * minute_coverage)
    )
    mature_reliability = _clamp(
        100.0 * (
            0.27 * daily_consistency
            + 0.18 * active_days
            + 0.20 * current_consistency
            + 0.35 * history_consistency
        ) * (0.70 + 0.30 * max(minute_coverage, h30["coverage"]))
    )
    reliability = cold_reliability * (1.0 - history_maturity) + mature_reliability * history_maturity

    current_strength = 0.65 * impulse_strength + 0.35 * frequency_score
    cold_score = 0.40 * daily_strength + 0.36 * current_strength + 0.24 * cold_reliability
    mature_score = 0.23 * daily_strength + 0.28 * current_strength + 0.39 * history_strength + 0.10 * mature_reliability
    score = cold_score * (1.0 - history_maturity) + mature_score * history_maturity
    # Single current events are already separated as SHK!/E. Persistent history
    # gets its own concentration discount, preventing one retained extreme window
    # from defining a coin for weeks.
    combined_penalty = (1.0 - history_maturity) * current_concentration_penalty + history_maturity * (
        0.35 * current_concentration_penalty + 0.65 * history_concentration_penalty
    )
    score *= 1.0 - 0.24 * combined_penalty
    score = _clamp(score, 0.0, 99.0)

    return SpringerResult(
        available=True,
        score=round(score, 4),
        daily_range_pct=round(typical, 6),
        recent_daily_range_pct=round(recent, 6),
        intraday_impulse_pct=round(impulse_typical, 6),
        impulse_frequency=round(frequency, 6),
        reliability=round(reliability, 4),
        concentration=round(concentration, 6),
        minute_coverage=round(minute_coverage, 6),
        daily_samples=len(ranges),
        minute_samples=len(minute_rows),
        history_samples=history_samples,
        history_days=round(history_days, 4),
        history_coverage=round(h30["coverage"], 6),
        history_impulse_pct_7d=round(h7["typical"], 6),
        history_impulse_pct_30d=round(h30["typical"], 6),
        history_frequency_7d=round(h7["frequency"], 6),
        history_frequency_30d=round(h30["frequency"], 6),
        reason="persistent 15m impulses (7/30d) + current tape + robust daily range; one-off concentration discounted",
    )
