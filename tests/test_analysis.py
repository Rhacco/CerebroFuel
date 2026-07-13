from datetime import datetime, timedelta, timezone
import unittest

from analysis import (
    CoinAnalysis,
    PricePoint,
    Seasonality,
    analyze_coin,
    analyze_seasonality,
    build_report,
    combined_relative_mark,
    compute_volume_trends,
    delta_to_pct,
    marks_from_value,
)
from discord_sender import split_report


class AnalysisTests(unittest.TestCase):
    def test_delta_multiplier(self):
        self.assertAlmostEqual(delta_to_pct(1.08), 8.0)
        self.assertAlmostEqual(delta_to_pct(0.95), -5.0)

    def test_marks(self):
        self.assertEqual(marks_from_value(6, light=0.5, clear=2, strong=5), "+++")
        self.assertEqual(marks_from_value(-2.5, light=0.5, clear=2, strong=5), "--")
        self.assertEqual(marks_from_value(0.1, light=0.5, clear=2, strong=5), "=")

    def test_combined_relative_mark(self):
        self.assertIn(combined_relative_mark(3.0, 7.0), {"++", "+++"})
        self.assertIn(combined_relative_mark(-3.0, -7.0), {"--", "---"})

    def test_volume_trends(self):
        now = datetime.now(timezone.utc)
        points = []
        for hours_ago in range(0, 24 * 8):
            timestamp = int((now - timedelta(hours=hours_ago)).timestamp() * 1000)
            volume = 100.0 if hours_ago >= 18 else 150.0
            points.append(PricePoint(timestamp, 10.0, volume))
        v24, v7 = compute_volume_trends(160.0, points, int(now.timestamp() * 1000))
        self.assertIsNotNone(v24)
        self.assertGreater(v24, 10)
        self.assertIsNotNone(v7)

    def test_time_data_insufficient_is_question_mark(self):
        now = datetime.now(timezone.utc)
        points = [
            PricePoint(int((now - timedelta(hours=i)).timestamp() * 1000), 100 + i, 1000)
            for i in range(5)
        ]
        result = analyze_seasonality(points, now, "Europe/Berlin")
        self.assertEqual(result.current, "?")
        self.assertEqual(result.best_weekdays, tuple())

    def test_clear_buy_requires_volume(self):
        now = datetime.now(timezone.utc)
        points = []
        for hours_ago in range(24 * 90, -1, -1):
            timestamp = int((now - timedelta(hours=hours_ago)).timestamp() * 1000)
            rate = 100 + (24 * 90 - hours_ago) * 0.01
            volume = 100 if hours_ago > 30 else 160
            points.append(PricePoint(timestamp, rate, volume))
        current = {
            "rate": 130,
            "volume": 170,
            "delta": {"hour": 1.01, "day": 1.04, "week": 1.12},
        }
        result = analyze_coin(
            display_code="TEST",
            api_code="TEST",
            current=current,
            history=points,
            btc_day_pct=1.0,
            btc_week_pct=2.0,
            now=now,
            timezone="Europe/Berlin",
            block_hours=4,
            min_samples=4,
            recommendation_threshold=6,
        )
        self.assertGreaterEqual(result.buy_count, 5)
        self.assertEqual(result.recommendation, "BUY")

    def test_report_has_no_blank_lines_or_footer(self):
        ref = CoinAnalysis(
            display_code="BTC", api_code="BTC", price=1, hour_pct=0, day_pct=1,
            week_pct=2, relative_day_pct=0, relative_week_pct=0,
            volume_24h_pct=20, volume_7d_pct=30, day_mark="+", week_mark="+",
            relative_mark="=", pressure="+", seasonality=Seasonality("=", ("DI", "DO"), 100),
            buy_count=4, sell_count=0, direction="▲", recommendation="BUY", eligible=True,
            buy_flags={}, sell_flags={},
        )
        buy = CoinAnalysis(
            display_code="ETH", api_code="ETH", price=1, hour_pct=0, day_pct=3,
            week_pct=8, relative_day_pct=2, relative_week_pct=6,
            volume_24h_pct=30, volume_7d_pct=40, day_mark="++", week_mark="++",
            relative_mark="++", pressure="++", seasonality=Seasonality("+", ("MO", "DI", "DO"), 100),
            buy_count=8, sell_count=0, direction="▲", recommendation="BUY", eligible=True,
            buy_flags={}, sell_flags={},
        )
        report = build_report(ref, [[buy]])
        self.assertNotIn("\n\n", report)
        self.assertEqual(len(report.splitlines()), 2)
        self.assertTrue(report.splitlines()[1].startswith("🟢 ETH · 8/8▲"))

    def test_discord_split_has_no_blank_lines(self):
        text = "\n".join(f"COIN{i} " + "x" * 90 for i in range(30))
        chunks = split_report(text, 500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all("\n\n" not in chunk for chunk in chunks))
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
