from datetime import datetime, timedelta, timezone
import unittest

from analysis import (
    CoinAnalysis,
    PricePoint,
    Seasonality,
    analyze_seasonality,
    build_report,
    colored_mark,
    compute_short_momentum,
    compute_volume_trends,
    delta_to_pct,
    marks_from_value,
)
from discord_sender import split_report
from main import select_group_for_history


def coin(
    code: str,
    recommendation: str,
    count: int,
    *,
    eligible: bool,
    pressure: str,
    relative: float,
) -> CoinAnalysis:
    is_buy = recommendation == "BUY"
    return CoinAnalysis(
        display_code=code,
        api_code=code,
        price=1.0,
        short_pct=0.2 if is_buy else -0.2,
        hour_pct=0.5 if is_buy else -0.5,
        day_pct=2.0 if is_buy else -2.0,
        week_pct=5.0 if is_buy else -5.0,
        relative_day_pct=relative,
        relative_week_pct=relative * 2,
        volume_24h_pct=30.0,
        volume_7d_pct=25.0,
        day_mark="++" if is_buy else "--",
        week_mark="++" if is_buy else "--",
        relative_mark="++" if is_buy else "--",
        pressure=pressure,
        seasonality=Seasonality("+" if is_buy else "-", ("DI", "DO"), 200),
        buy_count=count if is_buy else 1,
        sell_count=count if not is_buy else 1,
        direction="▲" if is_buy else "▼",
        recommendation=recommendation,
        eligible=eligible,
        buy_flags={},
        sell_flags={},
        is_reference=False,
    )


class AnalysisTests(unittest.TestCase):
    def test_delta_multiplier(self):
        self.assertAlmostEqual(delta_to_pct(1.08), 8.0)
        self.assertAlmostEqual(delta_to_pct(0.95), -5.0)

    def test_internal_marks_and_colored_output(self):
        self.assertEqual(marks_from_value(6, light=0.5, clear=2, strong=5), "+++")
        self.assertEqual(colored_mark("+++"), "🟢🟢🟢")
        self.assertEqual(colored_mark("---"), "🔴🔴🔴")
        self.assertEqual(colored_mark("="), "🟡")

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

    def test_short_momentum_uses_fresh_history(self):
        now = datetime.now(timezone.utc)
        points = [
            PricePoint(int((now - timedelta(minutes=15)).timestamp() * 1000), 100.0, 1000.0),
            PricePoint(int((now - timedelta(minutes=5)).timestamp() * 1000), 101.0, 1100.0),
        ]
        result = compute_short_momentum(102.0, points, int(now.timestamp() * 1000))
        self.assertAlmostEqual(result, 2.0, places=2)

    def test_time_data_insufficient_is_question_mark(self):
        now = datetime.now(timezone.utc)
        points = [
            PricePoint(int((now - timedelta(hours=i)).timestamp() * 1000), 100 + i, 1000)
            for i in range(5)
        ]
        result = analyze_seasonality(points, now, "Europe/Berlin")
        self.assertEqual(result.current, "?")
        self.assertEqual(result.best_weekdays, tuple())

    def test_report_has_timestamp_colors_no_blank_lines_and_three_per_category(self):
        now = datetime(2026, 7, 13, 12, 4, tzinfo=timezone.utc)
        reference = coin("BTC", "BUY", 4, eligible=True, pressure="+", relative=0)
        reference.is_reference = True
        group = [
            coin(f"B{i}", "BUY", 8 - i, eligible=i < 2, pressure="++", relative=3 - i * 0.1)
            for i in range(5)
        ] + [
            coin(f"S{i}", "SELL", 8 - i, eligible=i < 2, pressure="--", relative=-3 + i * 0.1)
            for i in range(5)
        ]
        report = build_report(
            reference,
            [group],
            generated_at=now,
            timezone="UTC",
            min_per_category=3,
            max_per_category=6,
            watch_threshold=4,
        )
        lines = report.splitlines()
        self.assertEqual(len(lines), 7)
        self.assertIn("BTC@12:04", lines[0])
        self.assertNotIn("\n\n", report)
        self.assertNotIn("24h+", report)
        self.assertNotIn("24h-", report)
        self.assertTrue(any(line.startswith("🟢▲") for line in lines[1:]))
        self.assertTrue(any(line.startswith("🔴▼") for line in lines[1:]))
        self.assertTrue(any(line.startswith("🟡▲") for line in lines[1:]))
        self.assertTrue(any(line.startswith("🟡▼") for line in lines[1:]))

    def test_preselection_balances_buy_and_sell(self):
        group = [(f"C{i}", f"C{i}") for i in range(20)]
        current = {}
        for i in range(20):
            positive = i < 10
            current[f"C{i}"] = {
                "rate": 1,
                "delta": {
                    "hour": 1.01 if positive else 0.99,
                    "day": 1.05 if positive else 0.95,
                    "week": 1.10 if positive else 0.90,
                },
            }
        selected = select_group_for_history(
            group,
            current,
            limit=13,
            btc_day_pct=0.0,
            btc_week_pct=0.0,
        )
        self.assertEqual(len(selected), 13)
        selected_codes = {code for _, code in selected}
        self.assertTrue(any(int(code[1:]) < 10 for code in selected_codes))
        self.assertTrue(any(int(code[1:]) >= 10 for code in selected_codes))

    def test_discord_split_has_no_blank_lines(self):
        text = "\n".join(f"COIN{i} " + "x" * 90 for i in range(30))
        chunks = split_report(text, 500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all("\n\n" not in chunk for chunk in chunks))
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
