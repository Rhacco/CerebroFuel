from datetime import datetime, timedelta, timezone
import unittest

from analysis import (
    BLACK,
    BLUE,
    BROWN,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    WHITE,
    CoinAnalysis,
    PricePoint,
    Seasonality,
    ShortMetrics,
    analyze_seasonality,
    btc_gate,
    build_report,
    build_short_metrics,
    compute_window_changes,
    delta_to_pct,
    signed_color,
)
from discord_sender import split_report


CONFIG = {
    "snapshot_tolerance_minutes": 7,
    "minimum_reliable_volume_usd": 500_000,
    "maximum_plausible_volume_jump_pct": 500,
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
    "btc_no_drop_pct": {"10": -0.10, "20": -0.15, "60": -0.25},
}


def make_snapshots(now_ms, code="BTC", rate=100.0, volume=1_000_000.0):
    result = []
    for minutes, factor, vol_factor in [(60, 0.98, 0.97), (20, 0.99, 0.985), (10, 0.995, 0.995)]:
        result.append(
            {
                "ts": now_ms - minutes * 60_000,
                "coins": {code: {"rate": rate * factor, "volume": volume * vol_factor}},
            }
        )
    return result


def metrics(color=GREEN, direction="▲", count=6, gate=False):
    return ShortMetrics(
        price_changes={10: 0.4, 20: 0.7, 60: 1.2},
        volume_changes={10: 0.3, 20: 0.6, 60: 1.0},
        volume_colors={10: GREEN, 20: GREEN, 60: GREEN},
        relative_short_pct=0.5,
        relative_color=GREEN,
        pressure_score=1.5,
        pressure_color=color,
        buy_count=count if direction == "▲" else 1,
        sell_count=count if direction == "▼" else 1,
        direction=direction,
        signal_color=color,
        anomaly_score=10,
        data_quality="good",
    )


class AnalysisTests(unittest.TestCase):
    def test_delta_multiplier(self):
        self.assertAlmostEqual(delta_to_pct(1.08), 8.0)
        self.assertAlmostEqual(delta_to_pct(0.95), -5.0)

    def test_color_scale(self):
        self.assertEqual(signed_color(2, light=0.1, clear=0.5, strong=1.5), PURPLE)
        self.assertEqual(signed_color(0.7, light=0.1, clear=0.5, strong=1.5), GREEN)
        self.assertEqual(signed_color(0.2, light=0.1, clear=0.5, strong=1.5), BLUE)
        self.assertEqual(signed_color(-0.2, light=0.1, clear=0.5, strong=1.5), ORANGE)
        self.assertEqual(signed_color(-0.7, light=0.1, clear=0.5, strong=1.5), RED)
        self.assertEqual(signed_color(0.7, light=0.1, clear=0.5, strong=1.5, uncertain=True), BROWN)
        self.assertEqual(signed_color(None, light=0.1, clear=0.5, strong=1.5), WHITE)

    def test_window_changes(self):
        now_ms = 2_000_000_000_000
        snapshots = make_snapshots(now_ms)
        prices, volumes = compute_window_changes(
            api_code="BTC",
            current_rate=100,
            current_volume=1_000_000,
            snapshots=snapshots,
            now_ms=now_ms,
            tolerance_minutes=7,
        )
        self.assertGreater(prices[10], 0)
        self.assertGreater(volumes[60], 0)

    def test_btc_gate_green_only_when_all_windows_confirm(self):
        now_ms = 2_000_000_000_000
        current = {"rate": 100, "volume": 1_000_000, "delta": {"week": 1.02}}
        short = build_short_metrics(
            api_code="BTC",
            current=current,
            snapshots=make_snapshots(now_ms),
            now_ms=now_ms,
            btc_price_changes=None,
            config=CONFIG,
            is_reference=True,
        )
        self.assertTrue(btc_gate(short, CONFIG))
        short.volume_colors[20] = BLUE
        self.assertFalse(btc_gate(short, CONFIG))

    def test_insufficient_snapshots_are_white(self):
        now_ms = 2_000_000_000_000
        current = {"rate": 100, "volume": 1_000_000, "delta": {"week": 1.02}}
        short = build_short_metrics(
            api_code="BTC",
            current=current,
            snapshots=[],
            now_ms=now_ms,
            btc_price_changes=None,
            config=CONFIG,
            is_reference=True,
        )
        self.assertEqual(short.data_quality, "insufficient")
        self.assertEqual(short.signal_color, WHITE)
        self.assertTrue(all(value == WHITE for value in short.volume_colors.values()))

    def test_low_volume_is_brown(self):
        now_ms = 2_000_000_000_000
        current = {"rate": 100, "volume": 100_000, "delta": {"week": 1.02}}
        short = build_short_metrics(
            api_code="BTC",
            current=current,
            snapshots=make_snapshots(now_ms, volume=100_000),
            now_ms=now_ms,
            btc_price_changes=None,
            config=CONFIG,
            is_reference=True,
        )
        self.assertEqual(short.data_quality, "uncertain")
        self.assertEqual(short.signal_color, BROWN)

    def test_time_data_insufficient(self):
        now = datetime.now(timezone.utc)
        points = [
            PricePoint(int((now - timedelta(hours=i)).timestamp() * 1000), 100 + i, 1000)
            for i in range(5)
        ]
        result = analyze_seasonality(points, now, "Europe/Berlin")
        self.assertEqual(result.current, "?")

    def test_report_is_exactly_btc_plus_eight_and_compact(self):
        now = datetime(2026, 7, 13, 12, 1, tzinfo=timezone.utc)
        reference = CoinAnalysis(
            display_code="BTC",
            api_code="BTC",
            price=100,
            week_pct=2,
            week_color=BLUE,
            short=metrics(),
            seasonality=Seasonality("=", ("DI", "DO"), 200),
            is_reference=True,
            btc_gate=True,
        )
        coins = []
        for i in range(8):
            coins.append(
                CoinAnalysis(
                    display_code=f"C{i}",
                    api_code=f"C{i}",
                    price=1,
                    week_pct=2,
                    week_color=BLUE,
                    short=metrics(GREEN if i < 4 else RED, "▲" if i < 4 else "▼", 6),
                    seasonality=Seasonality("+", ("MO", "DI", "DO"), 200),
                )
            )
        report = build_report(reference, coins, generated_at=now, timezone="UTC")
        lines = report.splitlines()
        self.assertEqual(len(lines), 9)
        self.assertTrue(lines[0].startswith("₿12:01 🟢"))
        self.assertNotIn(" BTC", lines[0])
        self.assertNotIn("24h", report)
        self.assertNotIn("\n\n", report)
        self.assertIn("V🟢🟢🟢", report)

    def test_discord_split_has_no_blank_lines(self):
        text = "\n".join(f"COIN{i} " + "x" * 90 for i in range(30))
        chunks = split_report(text, 500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all("\n\n" not in chunk for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
