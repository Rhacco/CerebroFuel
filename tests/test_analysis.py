from datetime import datetime, timedelta, timezone
import unittest

from analysis import (
    PricePoint,
    analyze_seasonality,
    classify_comeback,
    classify_demand,
    delta_to_pct,
    format_price,
    pressure_marks,
    signal_from_score,
)


class AnalysisTests(unittest.TestCase):
    def test_delta_multiplier(self):
        self.assertAlmostEqual(delta_to_pct(1.08), 8.0)
        self.assertAlmostEqual(delta_to_pct(0.95), -5.0)

    def test_demand(self):
        self.assertEqual(classify_demand(5.0, 2.1), "+++")
        self.assertEqual(classify_demand(2.0, 1.6), "++")
        self.assertEqual(classify_demand(-2.0, 1.6), "--")
        self.assertEqual(classify_demand(0.2, 0.9), "=")

    def test_comeback(self):
        now = datetime.now(timezone.utc)
        points = []
        for hours_ago, rate in [(20, 100), (12, 90), (6, 95), (1, 104)]:
            timestamp = int((now - timedelta(hours=hours_ago)).timestamp() * 1000)
            points.append(PricePoint(timestamp, rate, 1_000_000))
        label, position = classify_comeback(
            105, 5.0, 1.0, points, int(now.timestamp() * 1000)
        )
        self.assertEqual(label, "+++")
        self.assertIsNotNone(position)

    def test_pressure_marks(self):
        self.assertEqual(pressure_marks(6.0, 0.5, 2.0, 5.0), "+++")
        self.assertEqual(pressure_marks(2.5, 0.5, 2.0, 5.0), "++")
        self.assertEqual(pressure_marks(-0.7, 0.5, 2.0, 5.0), "-")
        self.assertEqual(pressure_marks(0.1, 0.5, 2.0, 5.0), "=")

    def test_time_data_is_explicit_when_insufficient(self):
        now = datetime.now(timezone.utc)
        points = [
            PricePoint(int((now - timedelta(hours=3)).timestamp() * 1000), 100, None),
            PricePoint(int((now - timedelta(hours=2)).timestamp() * 1000), 101, None),
            PricePoint(int((now - timedelta(hours=1)).timestamp() * 1000), 102, None),
        ]
        result = analyze_seasonality(points, now, "Europe/Berlin")
        self.assertEqual(result.current, "?")
        self.assertEqual(result.best_weekday, "?")
        self.assertLess(result.samples, 20)

    def test_signal_thresholds(self):
        self.assertEqual(signal_from_score(3.0, 3.0, -2.5), ("EIN", "🟢"))
        self.assertEqual(signal_from_score(-2.5, 3.0, -2.5), ("AUS", "🔴"))
        self.assertEqual(signal_from_score(0.0, 3.0, -2.5), ("WARTEN", "🟡"))

    def test_price_format(self):
        self.assertEqual(format_price(1500), "$1.50k")
        self.assertEqual(format_price(0.12345), "$0.1235")


if __name__ == "__main__":
    unittest.main()
