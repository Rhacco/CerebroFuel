from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from analysis import (
    BLACK,
    BLUE,
    GREEN,
    PURPLE,
    RED,
    YELLOW,
    CoinAnalysis,
    PricePoint,
    Seasonality,
    ShortMetrics,
    abbreviate_code,
    analyze_seasonality,
    btc_gate,
    build_report,
    build_short_metrics,
    compute_window_changes_from_history,
)


CONFIG = {
    "price": {
        "10": {"light": 0.10, "clear": 0.30, "strong": 0.80},
        "20": {"light": 0.15, "clear": 0.50, "strong": 1.20},
        "60": {"light": 0.30, "clear": 1.00, "strong": 2.50},
    },
    "volume": {
        "10": {"light": 0.05, "clear": 0.20, "strong": 0.75},
        "20": {"light": 0.10, "clear": 0.35, "strong": 1.25},
        "60": {"light": 0.25, "clear": 0.75, "strong": 2.50},
    },
    "relative_light_pct": 0.12,
    "relative_clear_pct": 0.40,
    "relative_strong_pct": 1.20,
    "minimum_reliable_volume_usd": 500_000,
    "minimum_short_history_points": 4,
    "maximum_plausible_volume_jump_pct": 500,
    "btc_no_drop_pct": {"10": -0.10, "20": -0.15, "60": -0.25},
}


class AnalysisTests(unittest.TestCase):
    def test_abbreviations_are_at_most_three_chars(self) -> None:
        self.assertEqual(abbreviate_code("ETH"), "ETH")
        self.assertEqual(abbreviate_code("DOGE"), "DGE")
        self.assertEqual(abbreviate_code("HBAR"), "HBR")
        self.assertEqual(abbreviate_code("RENDER"), "RND")
        self.assertEqual(abbreviate_code("FARTCOIN"), "FRT")
        self.assertEqual(abbreviate_code("W"), "W")

    def test_short_history_calculates_all_windows(self) -> None:
        now = 1_800_000_000_000
        history = [
            PricePoint(now - 60 * 60_000, 100.0, 1_000_000.0),
            PricePoint(now - 20 * 60_000, 101.0, 1_010_000.0),
            PricePoint(now - 10 * 60_000, 102.0, 1_020_000.0),
        ]
        price, volume = compute_window_changes_from_history(
            current_rate=103.0,
            current_volume=1_030_000.0,
            history=history,
            now_ms=now,
        )
        self.assertTrue(all(price[window] is not None for window in (10, 20, 60)))
        self.assertTrue(all(volume[window] is not None for window in (10, 20, 60)))
        self.assertGreater(price[10], 0)
        self.assertGreater(volume[60], 0)

    def test_btc_gate_requires_rising_volume_and_no_drop(self) -> None:
        short = ShortMetrics(
            price_changes={10: 0.1, 20: 0.2, 60: 0.4},
            volume_changes={10: 0.3, 20: 0.5, 60: 1.0},
            volume_colors={10: GREEN, 20: GREEN, 60: PURPLE},
            relative_short_pct=0.0,
            relative_color=YELLOW,
            pressure_score=1.0,
            pressure_color=GREEN,
            buy_count=5,
            sell_count=0,
            direction="▲",
            signal_color=GREEN,
            anomaly_score=10.0,
            data_quality="good",
        )
        self.assertTrue(btc_gate(short, CONFIG))
        short.volume_colors[20] = YELLOW
        self.assertFalse(btc_gate(short, CONFIG))

    def test_adaptive_seasonality_never_returns_question_mark(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        points = []
        rate = 100.0
        for day in range(50):
            ts = now - timedelta(days=50 - day)
            rate *= 1.002 if ts.weekday() in (1, 3) else 0.999
            points.append(PricePoint(int(ts.timestamp() * 1000), rate, 1_000_000.0))
        result = analyze_seasonality(
            points,
            now,
            "Europe/Berlin",
            block_hours=4,
            min_samples=3,
            minimum_observations=20,
        )
        self.assertNotEqual(result.current, "?")
        self.assertGreaterEqual(len(result.best_weekdays), 1)
        self.assertLessEqual(len(result.best_weekdays), 2)

    def test_fresh_short_metrics_do_not_need_kv(self) -> None:
        now = 1_800_000_000_000
        history = [
            PricePoint(now - 60 * 60_000, 100.0, 1_000_000.0),
            PricePoint(now - 40 * 60_000, 100.5, 1_005_000.0),
            PricePoint(now - 20 * 60_000, 101.0, 1_010_000.0),
            PricePoint(now - 10 * 60_000, 101.5, 1_015_000.0),
        ]
        current = {
            "rate": 102.0,
            "volume": 1_020_000.0,
            "delta": {"hour": 1.02, "day": 1.04, "week": 1.08},
        }
        short = build_short_metrics(
            current=current,
            short_history=history,
            now_ms=now,
            btc_price_changes=None,
            config=CONFIG,
            is_reference=True,
        )
        self.assertNotEqual(short.data_quality, "insufficient")
        self.assertNotIn("⚪", short.volume_colors.values())

    def test_compact_report_has_only_one_space_after_reference_time(self) -> None:
        now = datetime(2026, 7, 13, 12, 1, tzinfo=timezone.utc)
        short = ShortMetrics(
            price_changes={10: 0.2, 20: 0.4, 60: 0.8},
            volume_changes={10: 0.3, 20: 0.5, 60: 1.0},
            volume_colors={10: GREEN, 20: GREEN, 60: BLUE},
            relative_short_pct=0.4,
            relative_color=GREEN,
            pressure_score=1.2,
            pressure_color=GREEN,
            buy_count=6,
            sell_count=0,
            direction="▲",
            signal_color=GREEN,
            anomaly_score=20.0,
            data_quality="good",
        )
        ref = CoinAnalysis(
            display_code="BTC",
            api_code="BTC",
            price=1.0,
            week_pct=2.0,
            week_color=BLUE,
            short=short,
            seasonality=Seasonality("=", ("DI", "DO"), 100, "weekday"),
            is_reference=True,
            btc_gate=False,
        )
        coin = CoinAnalysis(
            display_code="DOGE",
            api_code="DOGE",
            price=1.0,
            week_pct=-2.0,
            week_color=RED,
            short=short,
            seasonality=Seasonality("+", ("MO", "FR"), 100, "weekday"),
        )
        report = build_report(ref, [coin], generated_at=now, timezone="UTC")
        lines = report.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], BLACK + ":01 6/7▲7" + BLUE + "B" + BLACK + "P" + GREEN + "V" + GREEN + GREEN + BLUE + "N" + YELLOW + "DIDO")
        self.assertTrue(lines[1].startswith(GREEN + "DGE6/8▲7"))
        self.assertEqual(report.count(" "), 1)
        self.assertNotIn("·", report)
        self.assertNotIn("/" + "".join(("DI", "DO")), report)
        self.assertNotIn("\n\n", report)


if __name__ == "__main__":
    unittest.main()
