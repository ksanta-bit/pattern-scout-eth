from __future__ import annotations

import unittest

import pandas as pd

from pattern_scout.backtester import PatternScoutBacktester
from pattern_scout.config import DailyContextConfig, PatternScoutConfig


def make_base_session(day: str, base: float = 100.0) -> list[dict]:
    rows = []
    timestamps = pd.date_range(f"{day} 09:30", periods=78, freq="5min")
    price = base
    for ts in timestamps:
        rows.append(
            {
                "timestamp": ts,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1000,
            }
        )
    return rows


class PatternScoutTests(unittest.TestCase):
    def test_long_john_wick_reversal_hits_opening_range_target(self):
        rows = make_base_session("2025-01-02") + make_base_session("2025-01-03")
        data = pd.DataFrame(rows)
        day2 = data.index[data["timestamp"].dt.strftime("%Y-%m-%d").eq("2025-01-03")]
        custom = [
            (100.0, 100.2, 96.8, 97.0),
            (97.0, 97.2, 94.0, 94.2),
            (94.2, 94.9, 91.0, 94.6),
            (94.6, 96.0, 94.5, 95.5),
            (95.5, 98.0, 95.0, 97.8),
            (97.8, 100.5, 97.5, 100.2),
        ]
        for row_idx, candle in zip(day2[: len(custom)], custom):
            data.loc[row_idx, ["open", "high", "low", "close"]] = candle

        config = PatternScoutConfig(
            atr_period=1,
            atr_min_periods=1,
            opening_body_fraction_min=0.5,
            manipulation_threshold=0.2,
            daily_context=DailyContextConfig(enabled=False),
        )
        result = PatternScoutBacktester(config).run(data)

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.side, "long")
        self.assertEqual(trade.signal_type, "john_wick")
        self.assertEqual(trade.exit_reason, "target")
        self.assertGreater(trade.pnl, 0)

    def test_no_trade_when_opening_range_is_too_small(self):
        rows = make_base_session("2025-01-02") + make_base_session("2025-01-03")
        data = pd.DataFrame(rows)
        config = PatternScoutConfig(
            atr_period=1,
            atr_min_periods=1,
            manipulation_threshold=0.5,
            daily_context=DailyContextConfig(enabled=False),
        )
        result = PatternScoutBacktester(config).run(data)
        self.assertEqual(len(result.trades), 0)

    def test_daily_breakout_retest_context_allows_trade(self):
        rows = []
        for day, base in [
            ("2024-12-30", 89.0),
            ("2024-12-31", 89.2),
            ("2025-01-01", 88.8),
            ("2025-01-02", 92.0),
            ("2025-01-03", 100.0),
        ]:
            rows.extend(make_base_session(day, base=base))
        data = pd.DataFrame(rows)
        for day in ["2024-12-30", "2024-12-31", "2025-01-01"]:
            mask = data["timestamp"].dt.strftime("%Y-%m-%d").eq(day)
            data.loc[mask, "high"] = 91.0
            data.loc[mask, "low"] = 88.0
            data.loc[mask, "close"] = 90.0
        breakout_mask = data["timestamp"].dt.strftime("%Y-%m-%d").eq("2025-01-02")
        data.loc[breakout_mask, "open"] = 91.0
        data.loc[breakout_mask, "high"] = 101.0
        data.loc[breakout_mask, "low"] = 90.5
        data.loc[breakout_mask, "close"] = 100.0

        setup_day = data.index[data["timestamp"].dt.strftime("%Y-%m-%d").eq("2025-01-03")]
        custom = [
            (100.0, 100.2, 96.8, 97.0),
            (97.0, 97.2, 94.0, 94.2),
            (94.2, 94.9, 91.0, 94.6),
            (94.6, 96.0, 94.5, 95.5),
            (95.5, 98.0, 95.0, 97.8),
            (97.8, 100.5, 97.5, 100.2),
        ]
        for row_idx, candle in zip(setup_day[: len(custom)], custom):
            data.loc[row_idx, ["open", "high", "low", "close"]] = candle

        config = PatternScoutConfig(
            atr_period=3,
            atr_min_periods=3,
            opening_body_fraction_min=0.5,
            manipulation_threshold=0.2,
            daily_context=DailyContextConfig(
                enabled=True,
                lookback_sessions=20,
                min_base_sessions=3,
                breakout_recent_sessions=2,
                retest_tolerance_atr_fraction=0.2,
            ),
        )
        result = PatternScoutBacktester(config).run(data)

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.daily_context_kind, "breakout_retest_support")
        self.assertAlmostEqual(trade.daily_context_level, 91.0)
        self.assertEqual(trade.exit_reason, "target")

    def test_daily_context_blocks_trade_without_breakout_retest(self):
        rows = []
        for day in pd.date_range("2024-12-30", "2025-01-03", freq="D"):
            rows.extend(make_base_session(day.strftime("%Y-%m-%d"), base=100.0))
        data = pd.DataFrame(rows)
        setup_day = data.index[data["timestamp"].dt.strftime("%Y-%m-%d").eq("2025-01-03")]
        custom = [
            (100.0, 100.2, 96.8, 97.0),
            (97.0, 97.2, 94.0, 94.2),
            (94.2, 94.9, 91.0, 94.6),
            (94.6, 96.0, 94.5, 95.5),
            (95.5, 98.0, 95.0, 97.8),
            (97.8, 100.5, 97.5, 100.2),
        ]
        for row_idx, candle in zip(setup_day[: len(custom)], custom):
            data.loc[row_idx, ["open", "high", "low", "close"]] = candle

        config = PatternScoutConfig(
            atr_period=3,
            atr_min_periods=3,
            opening_body_fraction_min=0.5,
            manipulation_threshold=0.2,
            daily_context=DailyContextConfig(enabled=True),
        )
        result = PatternScoutBacktester(config).run(data)

        self.assertEqual(len(result.trades), 0)


if __name__ == "__main__":
    unittest.main()
