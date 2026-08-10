# r1
"""Historical springer strength (J00..J99) for CF v7.0.0.

J measures recurring *normal* movement opportunity, not direction and not event
risk.  It deliberately combines recent intraday impulse frequency/speed with
multi-day range consistency and discounts one-off concentration.  Acute events
are represented separately by E and SHK!.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


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
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_springer_strength(
    *,
    minute_candles: Sequence[Mapping[str, Any]],
    daily_candles: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> SpringerResult:
    if not bool(config.get("springer_enabled", True)):
        return SpringerResult(reason="disabled")

    minute_need = max(120, int(config.get("springer_minute_lookback_minutes", 300)))
    minute_min = max(90, int(config.get("springer_min_contiguous_minutes", 180)))
    minute_rows = _latest_contiguous_minutes(minute_candles)
    minute_rows = minute_rows[-(minute_need + 1):]
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
            minute_samples=len(minute_rows),
            daily_samples=len(daily_rows),
            reason="insufficient contiguous daily history",
        )

    ranges = [_daily_range(row) for row in daily_rows]
    ranges = [value for value in ranges if value > 0]
    if len(ranges) < 10:
        return SpringerResult(
            minute_samples=len(minute_rows), daily_samples=len(ranges),
            reason="invalid daily range history",
        )

    typical = statistics.median(ranges)
    recent = statistics.median(ranges[-min(7, len(ranges)):])
    mad = statistics.median(abs(value - typical) for value in ranges)
    daily_consistency = _clamp(1.0 - mad / max(typical, 1e-9), 0.0, 1.0)
    # "Active day" is anchored to an absolute movement floor as well as the
    # coin's own median. This keeps a perfectly consistent but nearly flat coin
    # from receiving a high reliability score merely for being consistently flat.
    active_days = sum(value >= max(1.50, 0.70 * typical) for value in ranges) / len(ranges)
    recency_ratio = _clamp(recent / max(typical, 1e-9), 0.50, 1.75)

    # Stable absolute scale: ~4% median daily high/low range is clearly active;
    # ~8% is already a strong natural springer. Saturation keeps meme outliers
    # bounded and the median prevents a single event day from defining J.
    daily_strength = 99.0 * (1.0 - math.exp(-max(0.0, typical) / 4.0))
    daily_strength *= 0.85 + 0.15 * _clamp((recency_ratio - 0.50) / 1.25, 0.0, 1.0)

    closes = [_f(row.get("c")) for row in minute_rows]
    # Rolling 15-minute moves sampled every five minutes give many observations
    # without pretending highly-overlapping windows are independent.
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

    # Frequency must not be made harder to achieve merely because a coin has a
    # large daily range. Use its robust recent 1m noise plus a fixed 12 bp floor
    # instead. J then measures whether meaningful 15m impulses actually recur.
    one_minute = [abs(_pct(a, b)) for a, b in zip(closes, closes[1:])]
    minute_med = statistics.median(one_minute) if one_minute else 0.0
    minute_mad = statistics.median(abs(value - minute_med) for value in one_minute) if one_minute else 0.0
    robust_minute = max(0.005, minute_med + 1.4826 * minute_mad)
    meaningful_threshold = max(0.12, min(0.45, robust_minute * math.sqrt(15.0) * 0.90))
    meaningful = [value for value in impulses if value >= meaningful_threshold]
    frequency = len(meaningful) / len(impulses)
    # Absolute 15m scale is intentional: roughly 0.5-0.8% recurring movement is
    # already very useful for this monitor irrespective of the coin's daily range.
    impulse_strength = 99.0 * (1.0 - math.exp(-impulse_typical / 0.45))
    frequency_score = _clamp(frequency / 0.50 * 99.0)

    total_impulse = sum(impulses)
    concentration = max(impulses) / total_impulse if total_impulse > 0 else 0.0
    # A single exceptional candle is an event/shock, not normal J strength.
    concentration_penalty = _clamp((concentration - 0.18) / 0.32, 0.0, 1.0)

    meaningful_signed = [move for move in signed if abs(move) >= meaningful_threshold]
    if meaningful_signed:
        up = sum(1 for value in meaningful_signed if value > 0)
        down = sum(1 for value in meaningful_signed if value < 0)
        direction_balance = 2.0 * min(up, down) / max(1, up + down)
    else:
        direction_balance = 0.0

    # Reliability rewards repeated active days and recurring intraday impulses;
    # it does not require perfect two-sided symmetry because trends can still be
    # genuine springer phases.
    intraday_consistency = _clamp(
        0.62 * min(1.0, frequency / 0.35)
        + 0.15 * direction_balance
        + 0.23 * (1.0 - concentration_penalty),
        0.0,
        1.0,
    )
    minute_coverage = _clamp(len(minute_rows) / max(1, minute_need + 1), 0.0, 1.0)
    reliability = _clamp(
        (0.38 * daily_consistency + 0.28 * active_days + 0.34 * intraday_consistency)
        * 100.0
        * (0.75 + 0.25 * minute_coverage)
    )

    score = (
        0.40 * daily_strength
        + 0.36 * (0.65 * impulse_strength + 0.35 * frequency_score)
        + 0.24 * reliability
    )
    score *= 1.0 - 0.28 * concentration_penalty
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
        reason="normal recurring 15m impulses plus robust 7-30d range/reliability; one-off concentration discounted",
    )
