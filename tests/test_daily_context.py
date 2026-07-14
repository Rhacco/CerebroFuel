"""Daily cache, conservative bootstrap and flash-ranking tests for v3.2.6."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from analysis import CoinAnalysis, Seasonality, confidence_sort_key
from daily_context import _stable_days, local_day_key, seasonality_from_dict
from test_analysis import make_short

ROOT = Path(__file__).resolve().parents[1]


class DailyContextTests(unittest.TestCase):
    def test_strict_bootstrap_can_show_a_high_quality_day_immediately(self) -> None:
        raw = Seasonality(
            "+",
            ("DI",),
            350,
            "test",
            weekday_scores={"DI": 0.10},
            weekday_confidence={"DI": 0.74},
        )
        selected, enter, exit_, initialized, mode = _stable_days(raw, None, enter_days=2, exit_days=2)
        self.assertEqual(selected, ("DI",))
        self.assertEqual(enter["DI"], 1)
        self.assertEqual(exit_["DI"], 0)

    def test_robust_first_day_initializes_immediately(self) -> None:
        raw = Seasonality(
            "+",
            ("MO",),
            350,
            "test",
            weekday_scores={"MO": 0.030},
            weekday_confidence={"MO": 0.55},
        )
        first, enter, exit_, initialized, mode = _stable_days(raw, None, enter_days=2, exit_days=2)
        self.assertEqual(first, ("MO",))
        self.assertTrue(initialized)
        self.assertEqual(mode, "bootstrap-immediate")
        self.assertEqual(enter["MO"], 1)
        self.assertEqual(exit_["MO"], 0)

    def test_new_day_after_initialization_needs_two_daily_confirmations(self) -> None:
        raw = Seasonality(
            "+",
            ("MO",),
            350,
            "test",
            weekday_scores={"MO": 0.060},
            weekday_confidence={"MO": 0.61},
        )
        previous = {
            "weekday_initialized": True,
            "stable_best_weekdays": [],
            "enter_streaks": {},
            "exit_streaks": {},
        }
        first, enter, exit_, initialized, mode = _stable_days(raw, previous, enter_days=2, exit_days=2)
        self.assertEqual(first, tuple())
        self.assertTrue(initialized)
        self.assertEqual(mode, "daily-hysteresis")
        previous2 = {
            "weekday_initialized": True,
            "stable_best_weekdays": [],
            "enter_streaks": enter,
            "exit_streaks": exit_,
        }
        second, _, _, _, _ = _stable_days(raw, previous2, enter_days=2, exit_days=2)
        self.assertEqual(second, ("MO",))

    def test_selected_day_needs_two_daily_failures_to_disappear(self) -> None:
        raw_none = Seasonality("=", tuple(), 350, "test")
        previous = {
            "weekday_initialized": True,
            "stable_best_weekdays": ["FR"],
            "enter_streaks": {"FR": 2},
            "exit_streaks": {"FR": 0},
            "weekday_scores": {"FR": 0.08},
            "weekday_confidence": {"FR": 0.65},
        }
        first, enter, exit_, _, _ = _stable_days(raw_none, previous, enter_days=2, exit_days=2)
        self.assertEqual(first, ("FR",))
        previous2 = {
            "weekday_initialized": True,
            "stable_best_weekdays": list(first),
            "enter_streaks": enter,
            "exit_streaks": exit_,
            "weekday_scores": {"FR": 0.08},
            "weekday_confidence": {"FR": 0.65},
        }
        second, _, _, _, _ = _stable_days(raw_none, previous2, enter_days=2, exit_days=2)
        self.assertEqual(second, tuple())

    def test_local_day_key_uses_configured_timezone(self) -> None:
        now = datetime(2026, 7, 14, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(local_day_key(now, "Europe/Berlin"), "2026-07-15")

    def test_seasonality_roundtrip(self) -> None:
        parsed = seasonality_from_dict(
            {
                "current": "+",
                "best_weekdays": ["SA", "DI"],
                "samples": 360,
                "source": "cache",
                "weekday_scores": {"SA": 0.11},
                "weekday_confidence": {"SA": 0.72},
            }
        )
        self.assertEqual(parsed.best_weekdays, ("SA", "DI"))
        self.assertEqual(parsed.samples, 360)

    def test_flash_ranking_can_surface_fresh_setup_without_changing_color(self) -> None:
        confirmed = CoinAnalysis(
            "ETH",
            "ETH",
            1.0,
            0.0,
            "🟡",
            make_short(6, "🟢", proximity=65),
            Seasonality("=", tuple(), 300, "cache"),
            ranking_score=80.0,
            flash_score=30.0,
        )
        fresh = CoinAnalysis(
            "WIF",
            "WIF",
            1.0,
            0.0,
            "🟡",
            make_short(2, "🟠", proximity=30, direction="▼"),
            Seasonality("=", tuple(), 300, "cache"),
            ranking_score=95.0,
            flash_score=92.0,
        )
        self.assertGreater(confidence_sort_key(fresh), confidence_sort_key(confirmed))
        self.assertEqual(fresh.short.signal_color, "🟠")

    def test_workflow_uses_daily_restore_and_save_cache(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
        self.assertIn("actions/cache/restore@v4", workflow)
        self.assertIn("actions/cache/save@v4", workflow)
        self.assertIn("hashFiles('config.json', 'analysis.py', 'daily_context.py')", workflow)
        self.assertIn("seasonality-v326q2", workflow)


class DailyStateIntegrationTests(unittest.TestCase):
    def test_same_day_context_avoids_second_long_history_refresh(self) -> None:
        import tempfile
        from unittest.mock import patch

        import main

        class FakeClient:
            def __init__(self) -> None:
                self.calls = 0

            def get_history(self, code: str, start_ms: int, end_ms: int):
                self.calls += 1
                rows = []
                rate = 100.0
                volume = 1_000_000.0
                step = 86_400_000
                for index in range(401):
                    timestamp = start_ms + index * step
                    if timestamp > end_ms:
                        break
                    rate *= 1.0005 if index % 7 == 2 else 0.9999
                    volume *= 1.001 if index % 7 == 2 else 0.9998
                    rows.append({"date": timestamp, "rate": rate, "volume": volume, "cap": 1e9})
                return rows

        config = {
            "timezone": "Europe/Berlin",
            "daily_history_days": 400,
            "history_parallel_requests": 2,
            "time_block_hours": 4,
            "seasonality_min_samples": 20,
            "seasonality_min_observations": 180,
            "seasonality_lookback_days": 365,
            "weekday_enter_confirmations": 2,
            "weekday_exit_confirmations": 2,
        }
        now = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            with patch.object(main, "DAILY_STATE_PATH", state_path):
                first, failures, count = main.refresh_daily_state_if_needed(
                    client=client,
                    resolved_all=[("BTC", "BTC"), ("ETH", "ETH")],
                    now=now,
                    config=config,
                )
                self.assertEqual(count, 2)
                self.assertFalse(failures)
                self.assertEqual(first["date"], "2026-07-15")
                calls_after_first = client.calls
                second, failures2, count2 = main.refresh_daily_state_if_needed(
                    client=client,
                    resolved_all=[("BTC", "BTC"), ("ETH", "ETH")],
                    now=now,
                    config=config,
                )
                self.assertEqual(count2, 0)
                self.assertFalse(failures2)
                self.assertEqual(client.calls, calls_after_first)
                self.assertEqual(second["date"], first["date"])


if __name__ == "__main__":
    unittest.main()
