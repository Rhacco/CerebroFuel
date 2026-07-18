"""Stable v3.3.1 daily cache with reusable histories and weekday context."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from analysis import (
    DAY_NAMES,
    DISPLAY_WEEK_ORDER,
    DailyObservation,
    PricePoint,
    Seasonality,
    _clamp,
    _complete_week_observations,
    _completed_daily_observations,
    _daily_group_metrics,
    _leave_extremes_out_central,
    _robust_baseline,
    _robust_market_beta,
    _robust_z,
    _trimmed_mean,
    rolling_week_returns,
)

STATE_VERSION = "3.3.1"
STATE_REVISION = "complete-weeks-pool-neutral-r2-ranked"


def local_day_key(now: datetime, timezone: str) -> str:
    return now.astimezone(ZoneInfo(timezone)).date().isoformat()


def seasonality_from_dict(raw: Mapping[str, Any] | None) -> Seasonality:
    item = raw or {}
    return Seasonality(
        current=str(item.get("current", "?")),
        best_weekdays=tuple(str(value) for value in item.get("best_weekdays", []) if str(value)),
        samples=int(item.get("samples", 0)),
        source=str(item.get("source", "daily-cache-missing")),
        current_score=(float(item["current_score"]) if item.get("current_score") is not None else None),
        current_confidence=float(item.get("current_confidence", 0.0)),
        weekday_scores={str(key): float(value) for key, value in (item.get("weekday_scores") or {}).items()},
        weekday_confidence={
            str(key): float(value)
            for key, value in (item.get("weekday_confidence") or {}).items()
        },
    )


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def history_from_context(raw: Mapping[str, Any] | None) -> list[PricePoint]:
    points: list[PricePoint] = []
    for item in (raw or {}).get("history", []):
        try:
            timestamp, rate, volume = item
            points.append(
                PricePoint(
                    int(timestamp),
                    float(rate),
                    None if volume is None else float(volume),
                )
            )
        except (TypeError, ValueError):
            continue
    return sorted({point.timestamp_ms: point for point in points}.values(), key=lambda point: point.timestamp_ms)


def compact_history(points: Sequence[PricePoint], *, keep_after_ms: int) -> list[list[Any]]:
    return [
        [
            point.timestamp_ms,
            round(point.rate, 12),
            None if point.volume is None else round(point.volume, 4),
        ]
        for point in points
        if point.timestamp_ms >= keep_after_ms
    ]


def _streaks(raw: Mapping[str, Any] | None, field: str) -> dict[str, int]:
    source = (raw or {}).get(field) or {}
    return {name: max(0, int(source.get(name, 0))) for name in DAY_NAMES}


def _complete_common_weeks(items: Sequence[DailyObservation]) -> tuple[list[DailyObservation], int]:
    return _complete_week_observations(items)


def _pool_maps(
    observations: Mapping[str, Sequence[DailyObservation]],
    *,
    reference_display: str,
) -> tuple[dict[int, float], dict[int, float], dict[int, int]]:
    """Cross-sectional median score/return by date, excluding BTC.

    A minimum breadth avoids treating a thin set of recently listed coins as the market.
    """
    by_date: dict[int, list[DailyObservation]] = {}
    for display, items in observations.items():
        if display == reference_display:
            continue
        for item in items:
            by_date.setdefault(item.date_ordinal, []).append(item)

    score_map: dict[int, float] = {}
    price_map: dict[int, float] = {}
    breadth_map: dict[int, int] = {}
    for ordinal, items in by_date.items():
        usable = [item for item in items if math.isfinite(item.score) and math.isfinite(item.price_pct)]
        if len(usable) < 8:
            continue
        score_map[ordinal] = statistics.median(item.score for item in usable)
        price_map[ordinal] = statistics.median(item.price_pct for item in usable)
        breadth_map[ordinal] = len(usable)
    return score_map, price_map, breadth_map


def _market_neutral_observations(
    *,
    display: str,
    reference_display: str,
    observations: Mapping[str, Sequence[DailyObservation]],
    pool_score: Mapping[int, float],
    pool_price: Mapping[int, float],
    pool_breadth: Mapping[int, int],
) -> tuple[list[DailyObservation], int, float, float]:
    coin_complete, _ = _complete_common_weeks(observations.get(display, []))
    if display == reference_display:
        return coin_complete, len(coin_complete) // 7, 0.0, 0.0

    reference_complete, _ = _complete_common_weeks(observations.get(reference_display, []))
    reference_by_day = {item.date_ordinal: item for item in reference_complete}
    common = [
        item
        for item in coin_complete
        if item.date_ordinal in reference_by_day
        and item.date_ordinal in pool_score
        and item.date_ordinal in pool_price
    ]
    common, complete_weeks = _complete_common_weeks(common)
    if len(common) < 28:
        return common, complete_weeks, 1.0, 0.0

    coin_returns = [item.price_pct for item in common]
    btc_returns = [reference_by_day[item.date_ordinal].price_pct for item in common]
    beta = _robust_market_beta(coin_returns, btc_returns)

    btc_residuals = [
        item.price_pct - beta * reference_by_day[item.date_ordinal].price_pct
        for item in common
    ]
    pool_price_residuals = [item.price_pct - pool_price[item.date_ordinal] for item in common]
    pool_score_residuals = [item.score - pool_score[item.date_ordinal] for item in common]
    btc_base = _robust_baseline(btc_residuals, 0.35)
    pool_price_base = _robust_baseline(pool_price_residuals, 0.35)
    pool_score_base = _robust_baseline(pool_score_residuals, 0.25)

    adjusted_raw: list[float] = []
    breadth_values: list[int] = []
    for item, btc_residual, price_residual, score_residual in zip(
        common,
        btc_residuals,
        pool_price_residuals,
        pool_score_residuals,
    ):
        btc_z = _clamp(_robust_z(btc_residual, btc_base) or 0.0, -3.5, 3.5)
        price_z = _clamp(_robust_z(price_residual, pool_price_base) or 0.0, -3.5, 3.5)
        score_z = _clamp(_robust_z(score_residual, pool_score_base) or 0.0, -3.5, 3.5)
        breadth = int(pool_breadth.get(item.date_ordinal, 0))
        breadth_values.append(breadth)
        breadth_factor = _clamp((breadth - 6) / 20.0, 0.55, 1.0)
        adjusted_raw.append(
            breadth_factor
            * (
                0.38 * item.score
                + 0.27 * btc_z
                + 0.20 * score_z
                + 0.15 * price_z
            )
        )

    centre = 0.62 * statistics.median(adjusted_raw) + 0.38 * _trimmed_mean(adjusted_raw)
    adjusted = [
        DailyObservation(
            timestamp_ms=item.timestamp_ms,
            date_ordinal=item.date_ordinal,
            weekday=item.weekday,
            score=_clamp(score - centre, -4.0, 4.0),
            price_pct=item.price_pct,
            volume_pct=item.volume_pct,
            reliability=item.reliability,
        )
        for item, score in zip(common, adjusted_raw)
    ]
    median_breadth = statistics.median(breadth_values) if breadth_values else 0.0
    return adjusted, complete_weeks, beta, float(median_breadth)


def _analyze_adjusted_weekdays(
    *,
    observations: Sequence[DailyObservation],
    complete_weeks: int,
    now: datetime,
    timezone: str,
    source: str,
    min_weeks: int,
) -> Seasonality:
    if complete_weeks < min_weeks or len(observations) < min_weeks * 7:
        return Seasonality(
            "?",
            tuple(),
            len(observations),
            f"complete-weeks-insufficient:{complete_weeks}",
        )

    local_now = now.astimezone(ZoneInfo(timezone))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    now_ms = int(local_midnight.timestamp() * 1000)
    today = local_now.date().toordinal()
    cutoffs = {182: today - 182, 91: today - 91, 42: today - 42}
    by_weekday: dict[int, list[DailyObservation]] = {}
    for item in observations:
        by_weekday.setdefault(item.weekday, []).append(item)

    ranked: list[tuple[int, float, float, float, int]] = []
    weekday_scores: dict[str, float] = {}
    weekday_confidence: dict[str, float] = {}
    day_summary: dict[int, tuple[float, float]] = {}

    for weekday in range(7):
        items = by_weekday.get(weekday, [])
        recent = {
            days: [item for item in items if item.date_ordinal >= cutoff]
            for days, cutoff in cutoffs.items()
        }
        if (
            len(items) < min_weeks
            or len(recent[182]) < 7
            or len(recent[91]) < 3
            or len(recent[42]) < 2
        ):
            continue

        full = _daily_group_metrics(items, now_ms)
        r182 = _daily_group_metrics(recent[182], now_ms)
        r91 = _daily_group_metrics(recent[91], now_ms)
        r42 = _daily_group_metrics(recent[42], now_ms)
        leave_out = _leave_extremes_out_central(items, now_ms)
        full_central, full_hit, dispersion, full_consistency, full_wilson = full
        c182, hit182, _, consistency182, wilson182 = r182
        c91, hit91, _, consistency91, _ = r91
        c42, hit42, _, _, _ = r42

        sample_factor = min(1.0, len(items) / 36.0)
        confidence = sample_factor * (
            0.31
            + 0.22 * full_consistency
            + 0.17 * consistency182
            + 0.13 * consistency91
            + 0.09 * min(1.0, len(recent[42]) / 6.0)
            + 0.08 * min(1.0, complete_weeks / 40.0)
        )
        confidence *= 1.0 / (1.0 + 0.085 * dispersion)
        confidence = _clamp(confidence, 0.0, 1.0)

        regime = 0.40 * full_central + 0.31 * c182 + 0.20 * c91 + 0.09 * c42
        robust_effect = 0.70 * regime + 0.30 * leave_out
        conflict = 1.0
        if c91 < -0.08:
            conflict *= 0.68
        elif c91 < 0:
            conflict *= 0.88
        if c42 < -0.20:
            conflict *= 0.72
        elif c42 < -0.06:
            conflict *= 0.90

        hit_support = min(full_hit, hit182, max(hit91, 0.45))
        lower_bound = min(full_wilson, wilson182)
        quality = robust_effect * confidence * (
            0.48 + 0.31 * hit_support + 0.21 * lower_bound
        ) * conflict

        name = DAY_NAMES[weekday]
        weekday_scores[name] = round(float(quality), 6)
        weekday_confidence[name] = round(float(confidence), 6)
        day_summary[weekday] = (robust_effect, confidence)

        strong = (
            robust_effect > 0.055
            and full_hit >= 0.525
            and hit182 >= 0.505
            and hit91 >= 0.455
            and leave_out > 0.020
            and confidence >= 0.43
            and quality > 0.010
        )
        usable = (
            robust_effect > 0.006
            and full_hit >= 0.490
            and hit182 >= 0.470
            and hit91 >= 0.415
            and leave_out > -0.010
            and confidence >= 0.31
            and quality > 0.0008
        )
        tier = 2 if strong else (1 if usable else 0)
        if quality > 0 and confidence >= 0.29 and robust_effect > 0:
            ranked.append((weekday, quality, confidence, robust_effect, tier))

    ranked.sort(key=lambda item: (item[4], item[1], item[2], item[3]), reverse=True)
    selected: list[int] = []
    if ranked and ranked[0][4] >= 1:
        selected.append(ranked[0][0])
    elif ranked and ranked[0][1] >= 0.0025 and ranked[0][2] >= 0.36:
        selected.append(ranked[0][0])

    if selected and len(ranked) >= 2:
        first, second = ranked[0], ranked[1]
        # The user prefers two useful days. Suppress the runner-up only when the
        # leader is genuinely isolated across effect, quality and confidence.
        first_dominates = (
            first[1] >= max(0.060, second[1] * 4.5)
            and first[3] >= max(0.24, second[3] * 3.5)
            and first[2] >= second[2] + 0.14
        )
        second_useful = (
            second[1] > 0.0006
            and second[2] >= 0.29
            and second[3] > 0.0
        )
        if second_useful and not first_dominates:
            selected.append(second[0])

    selected.sort(key=DISPLAY_WEEK_ORDER.index)
    best_days = tuple(DAY_NAMES[index] for index in selected)

    current_summary = day_summary.get(local_now.weekday())
    if current_summary is None:
        current, current_score, current_confidence = "?", 0.0, 0.0
    else:
        current_score, current_confidence = current_summary
        if current_confidence < 0.40:
            current = "?"
        elif current_score >= 0.80:
            current = "++"
        elif current_score >= 0.10:
            current = "+"
        elif current_score <= -0.80:
            current = "--"
        elif current_score <= -0.10:
            current = "-"
        else:
            current = "="

    return Seasonality(
        current=current,
        best_weekdays=best_days,
        samples=len(observations),
        source=source,
        current_score=current_score,
        current_confidence=current_confidence,
        weekday_scores=weekday_scores,
        weekday_confidence=weekday_confidence,
    )


def _stable_days(
    raw: Seasonality,
    previous: Mapping[str, Any] | None,
    *,
    enter_days: int,
    exit_days: int,
) -> tuple[tuple[str, ...], dict[str, int], dict[str, int], bool, str]:
    previous = previous or {}
    previous_days = tuple(
        day for day in (str(value) for value in previous.get("stable_best_weekdays", []))
        if day in DAY_NAMES
    )
    initialized = bool(previous.get("weekday_initialized", False))
    enter = _streaks(previous, "enter_streaks")
    exit_ = _streaks(previous, "exit_streaks")
    raw_days = tuple(day for day in raw.best_weekdays if day in DAY_NAMES)
    raw_set = set(raw_days)

    for day in DAY_NAMES:
        if day in raw_set:
            enter[day] = enter.get(day, 0) + 1
            exit_[day] = 0
        else:
            enter[day] = 0
            exit_[day] = exit_.get(day, 0) + 1

    if not initialized:
        selected = list(raw_days[:2])
        mode = "bootstrap"
        initialized = bool(selected)
    else:
        selected = [day for day in previous_days if exit_.get(day, 0) < max(1, exit_days)]
        for day in raw_days:
            if day not in selected and enter.get(day, 0) >= max(1, enter_days):
                selected.append(day)
            if len(selected) >= 2:
                break
        mode = "daily-hysteresis"

    selected = list(dict.fromkeys(selected))[:2]
    selected.sort(key=lambda day: DISPLAY_WEEK_ORDER.index(DAY_NAMES.index(day)))
    return tuple(selected), enter, exit_, initialized, mode


def _diagnostics(
    raw: Seasonality,
    stable_days: Sequence[str],
    mode: str,
    *,
    complete_weeks: int,
    market_beta: float,
    market_breadth: float,
) -> dict[str, Any]:
    ranked = sorted(
        raw.weekday_scores,
        key=lambda day: (
            raw.weekday_scores.get(day, -999.0),
            raw.weekday_confidence.get(day, 0.0),
        ),
        reverse=True,
    )
    return {
        "mode": mode,
        "samples": raw.samples,
        "complete_weeks": complete_weeks,
        "market_beta": round(float(market_beta), 5),
        "market_breadth": round(float(market_breadth), 2),
        "source": raw.source,
        "raw": list(raw.best_weekdays),
        "stable": list(stable_days),
        "top": [
            {
                "day": day,
                "score": round(float(raw.weekday_scores.get(day, 0.0)), 6),
                "confidence": round(float(raw.weekday_confidence.get(day, 0.0)), 6),
                "selected": day in raw.best_weekdays,
            }
            for day in ranked[:7]
        ],
    }


def build_daily_contexts(
    *,
    histories: Mapping[str, Sequence[PricePoint]],
    api_codes: Mapping[str, str],
    reference_display: str,
    now: datetime,
    timezone: str,
    config: Mapping[str, Any],
    previous_coins: Mapping[str, Any] | None,
    computed_for: str,
    use_previous_hysteresis: bool,
) -> dict[str, dict[str, Any]]:
    lookback_days = int(config.get("seasonality_lookback_days", 300))
    daily_observations = {
        display: _completed_daily_observations(list(points), now, timezone, lookback_days)
        for display, points in histories.items()
    }
    pool_score, pool_price, pool_breadth = _pool_maps(
        daily_observations,
        reference_display=reference_display,
    )
    previous_coins = previous_coins or {}
    result: dict[str, dict[str, Any]] = {}
    min_weeks = max(8, int(config.get("seasonality_min_samples", 8)))

    for display, history in histories.items():
        adjusted, complete_weeks, beta, breadth = _market_neutral_observations(
            display=display,
            reference_display=reference_display,
            observations=daily_observations,
            pool_score=pool_score,
            pool_price=pool_price,
            pool_breadth=pool_breadth,
        )
        source = (
            f"complete-weeks-reference:{complete_weeks}"
            if display == reference_display
            else f"complete-weeks-btc-pool-neutral:{complete_weeks}:beta={beta:.3f}:breadth={breadth:.1f}"
        )
        raw = _analyze_adjusted_weekdays(
            observations=adjusted,
            complete_weeks=complete_weeks,
            now=now,
            timezone=timezone,
            source=source,
            min_weeks=min_weeks,
        )
        prior = previous_coins.get(display) if use_previous_hysteresis else None
        stable_days, enter, exit_, initialized, mode = _stable_days(
            raw,
            prior if isinstance(prior, Mapping) else None,
            enter_days=int(config.get("weekday_enter_confirmations", 2)),
            exit_days=int(config.get("weekday_exit_confirmations", 2)),
        )
        stable = Seasonality(
            current=raw.current,
            best_weekdays=stable_days,
            samples=raw.samples,
            source=f"daily-v331-ranked-{mode}",
            current_score=raw.current_score,
            current_confidence=raw.current_confidence,
            weekday_scores=raw.weekday_scores,
            weekday_confidence=raw.weekday_confidence,
        )
        diagnostics = _diagnostics(
            raw,
            stable_days,
            mode,
            complete_weeks=complete_weeks,
            market_beta=beta,
            market_breadth=breadth,
        )
        result[display] = {
            "display": display,
            "api_code": api_codes[display],
            "seasonality": asdict(stable),
            "raw_best_weekdays": list(raw.best_weekdays),
            "stable_best_weekdays": list(stable_days),
            "weekday_initialized": initialized,
            "enter_streaks": enter,
            "exit_streaks": exit_,
            "weekday_scores": raw.weekday_scores,
            "weekday_confidence": raw.weekday_confidence,
            "weekday_diagnostics": diagnostics,
            "week_returns": [round(float(value), 8) for value in rolling_week_returns(list(history))[-420:]],
            "history_points": len(history),
            "history": compact_history(
                history,
                keep_after_ms=int(
                    (now.timestamp() - (int(config.get("daily_history_days", 300)) + 21) * 86400)
                    * 1000
                ),
            ),
            "method_revision": STATE_REVISION,
            "status": "complete",
            "computed_for": computed_for,
            "last_error": None,
            "last_attempt_at": now.isoformat(),
        }
    return result



def volume_trend_from_context(
    state: Mapping[str, Any],
    display: str,
    *,
    current_volume: float | None,
    now_ms: int,
    days: int = 7,
) -> float | None:
    """Compare today's rolling LCW volume with the nearest cached value days ago.

    The daily cache already carries raw volume history, so this adds no monitor
    request. A broad tolerance is intentional because daily LCW points do not
    necessarily land at the same minute every day.
    """
    if current_volume is None or current_volume <= 0:
        return None
    raw = (state.get("coins") or {}).get(display) or {}
    history = history_from_context(raw)
    if not history:
        return None
    target_ms = int(now_ms - max(1, int(days)) * 86_400_000)
    tolerance_ms = 40 * 3_600_000
    candidates = [
        point for point in history
        if point.volume is not None
        and point.volume > 0
        and abs(point.timestamp_ms - target_ms) <= tolerance_ms
    ]
    if not candidates:
        return None
    previous = min(candidates, key=lambda point: abs(point.timestamp_ms - target_ms))
    return (float(current_volume) / float(previous.volume) - 1.0) * 100.0

def context_for_coin(state: Mapping[str, Any], display: str) -> tuple[Seasonality, list[float]]:
    raw = (state.get("coins") or {}).get(display) or {}
    seasonality = seasonality_from_dict(raw.get("seasonality"))
    returns = [float(value) for value in raw.get("week_returns", [])]
    return seasonality, returns
