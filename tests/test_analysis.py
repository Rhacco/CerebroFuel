"""Focused deterministic tests for crypto-signal-monitor v3.2.4."""

from __future__ import annotations

import json
import math
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import (
    BLUE,
    BROWN,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    WHITE,
    YELLOW,
    CoinAnalysis,
    PricePoint,
    RobustBaseline,
    Seasonality,
    ShortMetrics,
    abbreviate_code,
    analyze_seasonality,
    build_coin_analysis,
    build_report,
    build_short_metrics,
    compute_window_changes_from_history,
    confidence_sort_key,
    display_code,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
NOW_MS = 1_720_000_000_000


def calm_history(*, hours: int = 12, rate: float = 100.0, volume: float = 1_000_000.0) -> list[PricePoint]:
    points: list[PricePoint] = []
    count = hours * 12
    for index in range(count + 1):
        timestamp = NOW_MS - (count - index) * 5 * 60_000
        points.append(
            PricePoint(
                timestamp,
                rate * (1.0 + 0.00010 * math.sin(index / 5.0)),
                volume * (1.0 + 0.0010 * math.sin(index / 7.0)),
            )
        )
    return points


def make_short(
    count: int,
    signal: str,
    *,
    proximity: float = 50.0,
    direction: str = "▲",
) -> ShortMetrics:
    baselines = {window: RobustBaseline(0.0, 0.1, 20) for window in (10, 20, 60)}
    buy = count if direction == "▲" else 0
    sell = count if direction == "▼" else 0
    acc = proximity if direction == "▲" else 0.0
    dist = proximity if direction == "▼" else 0.0
    return ShortMetrics(
        price_changes={10: 0.1, 20: 0.2, 60: 0.3},
        volume_changes={10: 0.5, 20: 0.7, 60: 1.0},
        volume_colors={10: GREEN, 20: GREEN, 60: BLUE},
        relative_short_pct=0.2,
        relative_color=GREEN,
        pressure_score=1.2 if direction == "▲" else -1.2,
        pressure_color=GREEN if direction == "▲" else ORANGE,
        buy_count=buy,
        sell_count=sell,
        direction=direction,
        signal_color=signal,
        anomaly_score=proximity,
        data_quality="good",
        window_quality={10: "good", 20: "good", 60: "good"},
        window_setup_scores={10: 1.2, 20: 1.0, 60: 0.8},
        agreement_score=1.0,
        accumulation_windows={10: acc, 20: acc, 60: acc},
        distribution_windows={10: dist, 20: dist, 60: dist},
        accumulation_score=acc,
        distribution_score=dist,
        extreme_proximity=proximity,
        pattern_confidence=proximity / 100.0,
        acceleration_score=0.0,
        relative_window_scores={10: 1.0, 20: 1.0, 60: 0.5},
        price_baselines=baselines,
        volume_baselines=baselines,
        price_strengths={10: 1.0, 20: 1.0, 60: 0.5},
        volume_strengths={10: 2.0, 20: 2.0, 60: 1.5},
    )


class AnalysisTests(unittest.TestCase):
    def test_fixed_readable_aliases_without_padding(self) -> None:
        self.assertEqual(abbreviate_code("NEAR"), "NER")
        self.assertEqual(abbreviate_code("HBAR"), "HBR")
        self.assertEqual(abbreviate_code("DOGE"), "DGE")
        self.assertEqual(abbreviate_code("RENDER"), "RND")
        self.assertEqual(abbreviate_code("ZKSYNC"), "ZKS")
        self.assertEqual(abbreviate_code("ETHFI"), "EFI")
        self.assertEqual(abbreviate_code("MORPHO"), "MRP")
        self.assertEqual(display_code("W"), "W")
        self.assertEqual(display_code("OP"), "OP")

    def test_short_history_calculates_all_windows(self) -> None:
        history = calm_history()
        price, volume = compute_window_changes_from_history(
            current_rate=100.1,
            current_volume=1_010_000.0,
            history=history,
            now_ms=NOW_MS,
        )
        self.assertTrue(all(price[window] is not None for window in (10, 20, 60)))
        self.assertTrue(all(volume[window] is not None for window in (10, 20, 60)))

    def test_strict_accumulation_is_purple_and_high_count(self) -> None:
        short = build_short_metrics(
            current={
                "rate": 100.01,
                "volume": 1_030_000,
                "delta": {"hour": 1.0001, "day": 1.0, "week": 1.01},
            },
            short_history=calm_history(),
            now_ms=NOW_MS,
            btc_price_changes={10: 0.0, 20: 0.0, 60: 0.0},
            config=CONFIG,
            is_reference=False,
        )
        self.assertEqual(short.signal_color, PURPLE)
        self.assertEqual(short.pressure_color, PURPLE)
        self.assertGreaterEqual(short.buy_count, 6)
        self.assertGreaterEqual(short.accumulation_score, 82.0)

    def test_strict_distribution_is_red_and_high_count(self) -> None:
        short = build_short_metrics(
            current={
                "rate": 102.0,
                "volume": 985_000,
                "delta": {"hour": 1.02, "day": 1.03, "week": 1.05},
            },
            short_history=calm_history(),
            now_ms=NOW_MS,
            btc_price_changes={10: 0.0, 20: 0.0, 60: 0.0},
            config=CONFIG,
            is_reference=False,
        )
        self.assertEqual(short.signal_color, RED)
        self.assertEqual(short.pressure_color, RED)
        self.assertGreaterEqual(short.sell_count, 6)
        self.assertGreaterEqual(short.distribution_score, 82.0)

    def test_mixed_windows_interpolate_without_extreme_color(self) -> None:
        history = calm_history()
        # Only a mild price lead and almost flat volume: no strict extreme.
        short = build_short_metrics(
            current={
                "rate": 100.35,
                "volume": 1_001_000,
                "delta": {"hour": 1.0035, "day": 1.0, "week": 1.0},
            },
            short_history=history,
            now_ms=NOW_MS,
            btc_price_changes={10: 0.1, 20: 0.1, 60: 0.1},
            config=CONFIG,
            is_reference=False,
        )
        self.assertNotIn(short.signal_color, {PURPLE, RED})
        self.assertIn(short.signal_color, {GREEN, BLUE, YELLOW, ORANGE})

    def test_one_bad_volume_window_does_not_turn_whole_coin_brown(self) -> None:
        history = calm_history()
        # Corrupt only the nearest 10-minute point.
        target = NOW_MS - 10 * 60_000
        history = [
            PricePoint(point.timestamp_ms, point.rate, 1_000.0 if point.timestamp_ms == target else point.volume)
            for point in history
        ]
        short = build_short_metrics(
            current={"rate": 100.01, "volume": 1_000_000, "delta": {"week": 1.0}},
            short_history=history,
            now_ms=NOW_MS,
            btc_price_changes={10: 0.0, 20: 0.0, 60: 0.0},
            config=CONFIG,
            is_reference=False,
        )
        self.assertEqual(short.volume_colors[10], BROWN)
        self.assertNotEqual(short.volume_colors[20], BROWN)
        self.assertNotEqual(short.volume_colors[60], BROWN)
        self.assertNotEqual(short.signal_color, BROWN)
        self.assertIn(10, short.quality_reasons)

    def test_relative_extremes_require_consistency(self) -> None:
        history = calm_history()
        btc = build_short_metrics(
            current={"rate": 100.0, "volume": 1_000_000, "delta": {"week": 1.0}},
            short_history=history,
            now_ms=NOW_MS,
            btc_price_changes=None,
            config=CONFIG,
            is_reference=True,
        )
        adjusted = []
        for point in history:
            if point.timestamp_ms in {NOW_MS - 20 * 60_000, NOW_MS - 60 * 60_000}:
                adjusted.append(PricePoint(point.timestamp_ms, 100.45, point.volume))
            else:
                adjusted.append(point)
        coin = build_short_metrics(
            current={"rate": 100.45, "volume": 1_000_000, "delta": {"week": 1.0}},
            short_history=adjusted,
            now_ms=NOW_MS,
            btc_price_changes=btc.price_changes,
            btc_short=btc,
            config=CONFIG,
            is_reference=False,
        )
        # A single/mild relative impulse is not enough for purple/red.
        self.assertNotIn(coin.relative_color, {PURPLE, RED})

    def test_weekdays_need_120_day_consistency_and_only_positive_days(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        points: list[PricePoint] = []
        rate = 100.0
        volume = 1_000_000.0
        for day in range(130):
            timestamp = now - timedelta(days=130 - day)
            if timestamp.weekday() == 5:  # Samstag
                rate *= 1.008
                volume *= 1.015
            elif timestamp.weekday() == 1:  # Dienstag
                rate *= 1.005
                volume *= 1.009
            else:
                rate *= 0.998
                volume *= 0.997
            points.append(PricePoint(int(timestamp.timestamp() * 1000), rate, volume))
        result = analyze_seasonality(
            points,
            now,
            "Europe/Berlin",
            min_samples=12,
            minimum_observations=60,
        )
        self.assertEqual(result.best_weekdays, ("SA", "DI"))
        self.assertGreaterEqual(result.weekday_confidence["SA"], 0.48)
        self.assertNotIn("MO", result.best_weekdays)

    def test_no_forced_weekdays_when_none_are_reliably_positive(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        points: list[PricePoint] = []
        rate = 100.0
        volume = 1_000_000.0
        for day in range(130):
            timestamp = now - timedelta(days=130 - day)
            rate *= 0.999
            volume *= 0.999
            points.append(PricePoint(int(timestamp.timestamp() * 1000), rate, volume))
        result = analyze_seasonality(points, now, "Europe/Berlin", min_samples=12, minimum_observations=60)
        self.assertEqual(result.best_weekdays, tuple())

    def test_report_places_time_and_names_at_line_end_without_spaces(self) -> None:
        now = datetime(2026, 7, 13, 12, 1, tzinfo=timezone.utc)
        reference = CoinAnalysis(
            "BTC", "BTC", 1.0, 2.0, BLUE, make_short(6, GREEN, proximity=70),
            Seasonality("=", ("DI", "DO"), 100, "weekday"),
            now_color=YELLOW, is_reference=True, btc_gate=True,
        )
        eth = CoinAnalysis(
            "ETH", "ETH", 1.0, 2.0, GREEN, make_short(8, PURPLE, proximity=95),
            Seasonality("+", ("SA", "DI"), 100, "weekday"),
            now_color=GREEN,
        )
        report = build_report(reference, [eth], generated_at=now, timezone="UTC")
        lines = report.splitlines()
        self.assertTrue(lines[0].endswith("DIDO:01"))
        self.assertTrue(lines[1].endswith("SADIETH"))
        self.assertNotIn(" ", report)

    def test_sorting_prioritizes_extreme_proximity_before_count(self) -> None:
        high_proximity = CoinAnalysis(
            "WIF", "WIF", 1.0, 0.0, YELLOW, make_short(6, RED, proximity=94, direction="▼"),
            Seasonality("=", tuple(), 100, "weekday"),
        )
        high_count = CoinAnalysis(
            "ETH", "ETH", 1.0, 0.0, YELLOW, make_short(8, GREEN, proximity=65),
            Seasonality("=", tuple(), 100, "weekday"),
        )
        self.assertGreater(confidence_sort_key(high_proximity), confidence_sort_key(high_count))

    def test_build_coin_analysis_keeps_counts_at_eight(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        short = make_short(8, PURPLE, proximity=95)
        analysis = build_coin_analysis(
            display_code="ETH",
            api_code="ETH",
            current={"rate": 1.0, "delta": {"week": 1.04}},
            short=short,
            history=[],
            now=now,
            timezone="UTC",
            block_hours=4,
            min_samples=12,
            minimum_observations=60,
            is_reference=False,
            config=CONFIG,
        )
        self.assertLessEqual(analysis.short.buy_count, 8)
        self.assertLessEqual(analysis.short.sell_count, 8)


if __name__ == "__main__":
    unittest.main()
