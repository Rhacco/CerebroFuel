# Package revision: r1
"""Bounded 7/14/30-day regime context for CF v5.2.0.

The regime layer never creates a trade direction. It only adjusts an already
existing short-term signal by at most a configured number of score points.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Sequence

HORIZONS = (7, 14, 30)
WEIGHTS = {7: 0.50, 14: 0.30, 30: 0.20}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _timestamp_ms(row: Mapping[str, Any]) -> int:
    value = int(_f(row.get("t")))
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _pct(start: float, end: float) -> float:
    return 0.0 if start <= 0 else (end / start - 1.0) * 100.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _nearest_close(
    rows: Sequence[Mapping[str, Any]],
    target_ms: int,
    *,
    tolerance_hours: float = 40.0,
) -> float | None:
    candidates: list[tuple[int, float]] = []
    tolerance_ms = int(tolerance_hours * 3_600_000)
    for row in rows:
        stamp = _timestamp_ms(row)
        close = _f(row.get("c"))
        if stamp > 0 and close > 0 and abs(stamp - target_ms) <= tolerance_ms:
            candidates.append((abs(stamp - target_ms), close))
    return min(candidates, default=(0, 0.0), key=lambda item: item[0])[1] or None


def _horizon_returns(
    rows: Sequence[Mapping[str, Any]],
    current_price: float,
    now_ms: int,
) -> dict[int, float | None]:
    result: dict[int, float | None] = {}
    for days in HORIZONS:
        historical = _nearest_close(rows, now_ms - days * 86_400_000)
        result[days] = None if historical is None else _pct(historical, current_price)
    return result


def _normalised(value: float, scale: float) -> float:
    return 100.0 * math.tanh(value / max(scale, 1e-9))


def _consistency(values: Mapping[int, float], deadbands: Mapping[int, float]) -> float:
    weighted = sum(WEIGHTS[days] * values[days] for days in HORIZONS)
    dominant = 1 if weighted > 0 else -1 if weighted < 0 else 0
    if dominant == 0:
        return 0.0
    agreeing = sum(
        1
        for days in HORIZONS
        if abs(values[days]) >= deadbands[days]
        and (1 if values[days] > 0 else -1) == dominant
    )
    return agreeing / len(HORIZONS)


def _return_over_minutes(rows: Sequence[Mapping[str, Any]], minutes: int) -> float | None:
    clean = [row for row in rows if _f(row.get("c")) > 0 and _timestamp_ms(row) > 0]
    if len(clean) < minutes + 1:
        return None
    return _pct(_f(clean[-minutes - 1].get("c")), _f(clean[-1].get("c")))


def _btc_rebound(
    rows: Sequence[Mapping[str, Any]],
    *,
    lookback_minutes: int,
    minimum_drop_pct: float,
    minimum_rebound_pct: float,
    minimum_recovery_fraction: float,
) -> dict[str, float | int] | None:
    clean = [row for row in rows if _f(row.get("c")) > 0 and _timestamp_ms(row) > 0]
    clean = clean[-(lookback_minutes + 1):]
    if len(clean) < max(12, lookback_minutes // 2):
        return None
    closes = [_f(row.get("c")) for row in clean]
    low_index = min(range(len(closes)), key=closes.__getitem__)
    if low_index <= 0 or low_index >= len(closes) - 1:
        return None
    peak_before = max(closes[:low_index])
    low = closes[low_index]
    current = closes[-1]
    drop = max(0.0, -_pct(peak_before, low))
    rebound = max(0.0, _pct(low, current))
    recovery_fraction = rebound / max(drop, 1e-9)
    if (
        drop < minimum_drop_pct
        or rebound < minimum_rebound_pct
        or recovery_fraction < minimum_recovery_fraction
    ):
        return None
    return {
        "anchor_ms": _timestamp_ms(clean[low_index]),
        "drop_pct": drop,
        "rebound_pct": rebound,
        "recovery_fraction": recovery_fraction,
    }


def _close_near_timestamp(
    rows: Sequence[Mapping[str, Any]],
    target_ms: int,
    tolerance_minutes: int = 3,
) -> float | None:
    tolerance = tolerance_minutes * 60_000
    candidates = [
        (abs(_timestamp_ms(row) - target_ms), _f(row.get("c")))
        for row in rows
        if _timestamp_ms(row) > 0
        and _f(row.get("c")) > 0
        and abs(_timestamp_ms(row) - target_ms) <= tolerance
    ]
    return min(candidates, default=(0, 0.0), key=lambda item: item[0])[1] or None


@dataclass(frozen=True)
class RegimeResult:
    symbol: str
    available: bool = False
    score: float = 0.0
    consistency: float = 0.0
    modifier: float = 0.0
    return_7d: float | None = None
    return_14d: float | None = None
    return_30d: float | None = None
    relative_7d: float | None = None
    relative_14d: float | None = None
    relative_30d: float | None = None
    btc_rebound_pct: float | None = None
    rebound_participation: float | None = None
    relative_drift_60m: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_regimes(
    *,
    signals: Sequence[Any],
    minute_snapshots: Mapping[str, Mapping[str, Any]],
    daily_candles: Mapping[str, Sequence[Mapping[str, Any]]],
    now_ms: int,
    config: Mapping[str, Any],
) -> dict[str, RegimeResult]:
    by_symbol = {str(item.symbol).upper(): item for item in signals}
    btc = by_symbol.get("BTC")
    if btc is None:
        return {}

    returns: dict[str, dict[int, float | None]] = {}
    for symbol, signal in by_symbol.items():
        returns[symbol] = _horizon_returns(
            daily_candles.get(symbol, []),
            float(getattr(signal, "price", 0.0)),
            now_ms,
        )

    btc_returns = returns.get("BTC", {})
    rebound_cfg = config.get("regime") or {}
    btc_rebound = _btc_rebound(
        list((minute_snapshots.get("BTC") or {}).get("candles") or []),
        lookback_minutes=int(rebound_cfg.get("rebound_lookback_minutes", 60)),
        minimum_drop_pct=float(rebound_cfg.get("rebound_min_drop_pct", 0.35)),
        minimum_rebound_pct=float(rebound_cfg.get("rebound_min_rebound_pct", 0.18)),
        minimum_recovery_fraction=float(rebound_cfg.get("rebound_min_recovery_fraction", 0.25)),
    )
    absolute_scales = {7: 6.0, 14: 10.0, 30: 16.0}
    relative_scales = {7: 5.0, 14: 8.0, 30: 12.0}
    deadbands = {7: 0.8, 14: 1.2, 30: 1.8}
    maximum_modifier = float(rebound_cfg.get("maximum_modifier_points", 10.0))
    multiweek_cap = min(maximum_modifier, float(rebound_cfg.get("multiweek_modifier_cap", 8.0)))
    results: dict[str, RegimeResult] = {}

    for symbol, signal in by_symbol.items():
        own = returns.get(symbol, {})
        if any(own.get(days) is None for days in HORIZONS):
            results[symbol] = RegimeResult(symbol=symbol, reason="7/14/30D-Daten unvollständig")
            continue
        if symbol != "BTC" and any(btc_returns.get(days) is None for days in HORIZONS):
            results[symbol] = RegimeResult(symbol=symbol, reason="BTC-Referenzdaten unvollständig")
            continue

        own_values = {days: float(own[days]) for days in HORIZONS}
        relative_values = (
            own_values
            if symbol == "BTC"
            else {
                days: own_values[days] - float(btc_returns[days])
                for days in HORIZONS
            }
        )
        scales = absolute_scales if symbol == "BTC" else relative_scales
        normalised = {
            days: _normalised(relative_values[days], scales[days])
            for days in HORIZONS
        }
        consistency = _consistency(relative_values, deadbands)
        consistency_factor = 0.55 + 0.45 * consistency
        score = _clamp(
            sum(WEIGHTS[days] * normalised[days] for days in HORIZONS)
            * consistency_factor,
            -100.0,
            100.0,
        )

        direction = 1 if float(getattr(signal, "direction", 0.0)) >= 0 else -1
        multiweek_modifier = _clamp(
            direction * score / 100.0 * multiweek_cap,
            -multiweek_cap,
            multiweek_cap,
        )
        participation: float | None = None
        rebound_modifier = 0.0
        rebound_pct: float | None = None
        if symbol != "BTC" and btc_rebound is not None:
            anchor = _close_near_timestamp(
                list((minute_snapshots.get(symbol) or {}).get("candles") or []),
                int(btc_rebound["anchor_ms"]),
            )
            current = float(getattr(signal, "price", 0.0))
            rebound_pct = float(btc_rebound["rebound_pct"])
            if anchor is not None and current > 0 and rebound_pct > 0:
                coin_move = _pct(anchor, current)
                participation = _clamp(coin_move / rebound_pct, -3.0, 3.0)
                relative_rebound_bias = _clamp((participation - 1.0) * 25.0, -35.0, 35.0)
                rebound_modifier = direction * relative_rebound_bias / 17.5

        drift: float | None = None
        drift_modifier = 0.0
        if symbol != "BTC":
            own_60 = _return_over_minutes(
                list((minute_snapshots.get(symbol) or {}).get("candles") or []),
                60,
            )
            btc_60 = _return_over_minutes(
                list((minute_snapshots.get("BTC") or {}).get("candles") or []),
                60,
            )
            if own_60 is not None and btc_60 is not None:
                drift = own_60 - btc_60
                drift_score = _normalised(drift, float(rebound_cfg.get("relative_drift_scale_pct", 1.2)))
                drift_modifier = direction * drift_score / 66.6666667

        modifier = _clamp(
            multiweek_modifier + rebound_modifier + drift_modifier,
            -maximum_modifier,
            maximum_modifier,
        )
        results[symbol] = RegimeResult(
            symbol=symbol,
            available=True,
            score=round(score, 4),
            consistency=round(consistency, 4),
            modifier=round(modifier, 4),
            return_7d=round(own_values[7], 4),
            return_14d=round(own_values[14], 4),
            return_30d=round(own_values[30], 4),
            relative_7d=round(relative_values[7], 4),
            relative_14d=round(relative_values[14], 4),
            relative_30d=round(relative_values[30], 4),
            btc_rebound_pct=None if rebound_pct is None else round(rebound_pct, 4),
            rebound_participation=None if participation is None else round(participation, 4),
            relative_drift_60m=None if drift is None else round(drift, 4),
            reason="absolute BTC regime" if symbol == "BTC" else "BTC-relative regime",
        )
    return results


