from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from analysis import (
    BLACK,
    BLUE,
    BROWN,
    GREEN,
    PURPLE,
    RED,
    WHITE,
    YELLOW,
    CoinAnalysis,
    PricePoint,
    Seasonality,
    ShortMetrics,
    abbreviate_code,
    analyze_seasonality,
    btc_gate,
    build_coin_analysis,
    build_report,
    build_short_metrics,
    compute_window_changes_from_history,
    current_now_signal,
    display_code,
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
    "maximum_plausible_volume_jump_pct": 500,
    "now_signal": {"light": 0.35, "clear": 1.05, "strong": 2.20},
}

NOW_MS = 1_800_000_000_000


def make_short(
    count: int,
    signal_color: str,
    *,
    setup: dict[int, float | None] | None = None,
) -> ShortMetrics:
    return ShortMetrics(
        price_changes={10: 0.2, 20: 0.4, 60: 0.8},
        volume_changes={10: 0.3, 20: 0.5, 60: 1.0},
        volume_colors={10: GREEN, 20: GREEN, 60: BLUE},
        relative_short_pct=0.4,
        relative_color=GREEN,
        pressure_score=1.2,
        pressure_color=GREEN,
        buy_count=count,
        sell_count=0,
        direction="▲",
        signal_color=signal_color,
        anomaly_score=float(count),
        data_quality="good",
        window_setup_scores=setup or {10: 1.2, 20: 1.1, 60: 0.8},
        agreement_score=1.3,
    )


class AnalysisTests(unittest.TestCase):
    def test_fixed_readable_aliases_and_short_spacing(self) -> None:
        self.assertEqual(abbreviate_code("NEAR"), "NER")
        self.assertEqual(abbreviate_code("HBAR"), "HBR")
        self.assertEqual(abbreviate_code("DOGE"), "DGE")
        self.assertEqual(abbreviate_code("RENDER"), "RND")
        self.assertEqual(abbreviate_code("ZKSYNC"), "ZKS")
        self.assertEqual(abbreviate_code("ETHFI"), "EFI")
        self.assertEqual(abbreviate_code("MORPHO"), "MRP")
        self.assertEqual(display_code("W"), "W      ")
        self.assertEqual(display_code("OP"), "OP   ")

    def test_short_history_calculates_all_windows(self) -> None:
        history = [
            PricePoint(NOW_MS - 60 * 60_000, 100.0, 1_000_000.0),
            PricePoint(NOW_MS - 20 * 60_000, 101.0, 1_010_000.0),
            PricePoint(NOW_MS - 10 * 60_000, 102.0, 1_020_000.0),
        ]
        price, volume = compute_window_changes_from_history(
            current_rate=103.0,
            current_volume=1_030_000.0,
            history=history,
            now_ms=NOW_MS,
        )
        self.assertTrue(all(price[window] is not None for window in (10, 20, 60)))
        self.assertTrue(all(volume[window] is not None for window in (10, 20, 60)))

    def test_accumulation_is_scored_early_and_strongly(self) -> None:
        history = [
            PricePoint(NOW_MS - 60 * 60_000, 100.00, 1_000_000),
            PricePoint(NOW_MS - 20 * 60_000, 100.01, 1_010_000),
            PricePoint(NOW_MS - 10 * 60_000, 100.02, 1_015_000),
        ]
        current = {
            "rate": 100.03,
            "volume": 1_040_000,
            "delta": {"hour": 1.0003, "day": 1.0, "week": 1.01},
        }
        short = build_short_metrics(
            current=current,
            short_history=history,
            now_ms=NOW_MS,
            btc_price_changes={10: 0.0, 20: 0.0, 60: 0.0},
            config=CONFIG,
            is_reference=False,
        )
        score, color = current_now_signal(
            short,
            Seasonality("=", ("SA", "DI"), 100, "weekday", 0.0, 0.8),
            CONFIG,
            is_reference=False,
        )
        self.assertGreaterEqual(short.buy_count, 5)
        self.assertEqual(short.pressure_color, PURPLE)
        self.assertEqual(color, PURPLE)
        self.assertGreater(score or 0.0, 2.2)

    def test_price_outrunning_falling_volume_is_strong_sell_divergence(self) -> None:
        history = [
            PricePoint(NOW_MS - 60 * 60_000, 97.0, 1_050_000),
            PricePoint(NOW_MS - 20 * 60_000, 98.5, 1_040_000),
            PricePoint(NOW_MS - 10 * 60_000, 99.2, 1_030_000),
        ]
        current = {
            "rate": 100.0,
            "volume": 1_000_000,
            "delta": {"hour": 1.03, "day": 1.05, "week": 1.10},
        }
        short = build_short_metrics(
            current=current,
            short_history=history,
            now_ms=NOW_MS,
            btc_price_changes={10: 0.0, 20: 0.0, 60: 0.0},
            config=CONFIG,
            is_reference=False,
        )
        self.assertGreaterEqual(short.sell_count, 5)
        self.assertEqual(short.pressure_color, RED)
        self.assertTrue(all((value or 0) <= -2.0 for value in short.window_setup_scores.values()))

    def test_one_bad_volume_window_does_not_turn_whole_coin_brown(self) -> None:
        history = [
            PricePoint(NOW_MS - 60 * 60_000, 100.0, 800_000),
            PricePoint(NOW_MS - 20 * 60_000, 100.0, 900_000),
            PricePoint(NOW_MS - 10 * 60_000, 100.0, 1_000),
        ]
        current = {
            "rate": 100.01,
            "volume": 1_000_000,
            "delta": {"hour": 1.0, "day": 1.0, "week": 1.0},
        }
        short = build_short_metrics(
            current=current,
            short_history=history,
            now_ms=NOW_MS,
            btc_price_changes={10: 0.0, 20: 0.0, 60: 0.0},
            config=CONFIG,
            is_reference=False,
        )
        self.assertEqual(short.volume_colors[10], BROWN)
        self.assertNotEqual(short.volume_colors[20], BROWN)
        self.assertNotEqual(short.volume_colors[60], BROWN)
        self.assertEqual(short.data_quality, "good")
        self.assertNotEqual(short.pressure_color, BROWN)
        self.assertIn("Volumensprung", short.quality_reasons[10])

    def test_btc_gate_uses_two_of_three_setups(self) -> None:
        short = make_short(5, GREEN, setup={10: 2.8, 20: 1.5, 60: -0.4})
        self.assertTrue(btc_gate(short, CONFIG))
        short.window_setup_scores[60] = -2.3
        self.assertFalse(btc_gate(short, CONFIG))

    def test_weekdays_are_two_best_and_saturday_first_chronologically(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        points: list[PricePoint] = []
        rate = 100.0
        volume = 1_000_000.0
        for day in range(70):
            ts = now - timedelta(days=70 - day)
            if ts.weekday() in (5, 1):
                rate *= 1.006
                volume *= 1.012
            else:
                rate *= 0.999
                volume *= 0.999
            points.append(PricePoint(int(ts.timestamp() * 1000), rate, volume))
        result = analyze_seasonality(points, now, "Europe/Berlin", min_samples=3)
        self.assertEqual(len(result.best_weekdays), 2)
        self.assertEqual(result.best_weekdays[0], "SA")
        self.assertEqual(result.best_weekdays[1], "DI")

    def test_report_sort_and_new_compact_format(self) -> None:
        now = datetime(2026, 7, 13, 12, 1, tzinfo=timezone.utc)
        ref = CoinAnalysis(
            "BTC", "BTC", 1.0, 2.0, BLUE, make_short(6, GREEN),
            Seasonality("=", ("DI", "DO"), 100, "weekday"),
            now_color=YELLOW, is_reference=True, btc_gate=True,
        )
        low = CoinAnalysis(
            "W", "W", 1.0, 0.0, YELLOW, make_short(5, GREEN),
            Seasonality("=", ("MO", "FR"), 100, "weekday"), now_color=YELLOW,
        )
        high = CoinAnalysis(
            "ETH", "ETH", 1.0, 0.0, YELLOW, make_short(8, PURPLE),
            Seasonality("=", ("SA", "DI"), 100, "weekday"), now_color=GREEN,
        )
        mid = CoinAnalysis(
            "OP", "OP", 1.0, 0.0, YELLOW, make_short(7, GREEN),
            Seasonality("=", ("SO", "MI"), 100, "weekday"), now_color=BLUE,
        )
        lines = build_report(ref, [low, high, mid], generated_at=now, timezone="UTC").splitlines()
        self.assertEqual(lines[0], GREEN + " :01 6▲7" + BLUE + "B" + GREEN + "P" + GREEN + "V" + GREEN + GREEN + BLUE + "N" + YELLOW + "DIDO")
        self.assertIn("ETH8▲", lines[1])
        self.assertIn("OP   7▲", lines[2])
        self.assertIn("W      5▲", lines[3])
        self.assertNotIn("/8", "\n".join(lines[1:]))
        self.assertNotIn("\n\n", "\n".join(lines))

    def test_build_coin_analysis_finishes_exact_denominators(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        short = make_short(0, GREEN, setup={10: 2.8, 20: 2.0, 60: 1.2})
        current = {"rate": 1.0, "delta": {"week": 1.04}}
        analysis = build_coin_analysis(
            display_code="ETH",
            api_code="ETH",
            current=current,
            short=short,
            history=[],
            now=now,
            timezone="UTC",
            block_hours=4,
            min_samples=4,
            minimum_observations=20,
            is_reference=False,
            config=CONFIG,
        )
        self.assertLessEqual(analysis.short.buy_count, 8)
        self.assertLessEqual(analysis.short.sell_count, 8)


if __name__ == "__main__":
    unittest.main()

