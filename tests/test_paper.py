from __future__ import annotations

import unittest

import pandas as pd

from pattern_scout.backtester import PatternScoutBacktester
from pattern_scout.config import (
    DailyContextConfig,
    ExecutionConfig,
    PatternScoutConfig,
    RiskConfig,
)
from pattern_scout.paper import PaperBroker, replay_csv, size_position, liquidation_price
from pattern_scout.strategy import add_daily_atr, add_session_columns, normalize_ohlcv


def _session(day, base=100.0, custom=None):
    ts = pd.date_range(f"{day} 09:30", periods=78, freq="5min")
    rows = [{"timestamp": t, "open": base, "high": base + 0.5, "low": base - 0.5,
             "close": base, "volume": 1000} for t in ts]
    df = pd.DataFrame(rows)
    if custom:
        for i, c in enumerate(custom):
            df.loc[i, ["open", "high", "low", "close"]] = c
    return df


def _john_wick_frame():
    day1 = _session("2025-01-02")
    day2 = _session("2025-01-03", custom=[
        (100.0, 100.2, 96.8, 97.0),
        (97.0, 97.2, 94.0, 94.2),
        (94.2, 94.9, 91.0, 94.6),
        (94.6, 96.0, 94.5, 95.5),
        (95.5, 98.0, 95.0, 97.8),
        (97.8, 100.5, 97.5, 100.2),
    ])
    return pd.concat([day1, day2], ignore_index=True)


class ATRMethodTests(unittest.TestCase):
    def test_wilder_differs_from_sma_and_is_default(self):
        cfg = PatternScoutConfig()
        self.assertEqual(cfg.atr_method, "wilder")
        # Build multi-day data with VARYING daily ranges so SMA vs Wilder diverge.
        ranges = [1, 3, 2, 5, 1, 4, 2, 6]
        frames = []
        for i, rng in enumerate(ranges, start=1):
            b = 100 + i
            frames.append(_session(f"2025-01-{i:02d}", base=b,
                                    custom=[(b, b + rng, b - rng, b)]))
        data = pd.concat(frames, ignore_index=True)
        norm = add_session_columns(normalize_ohlcv(data, cfg), cfg)
        wil = add_daily_atr(norm, PatternScoutConfig(atr_method="wilder"))["atr"].dropna()
        sma = add_daily_atr(norm, PatternScoutConfig(atr_method="sma"))["atr"].dropna()
        self.assertFalse(wil.reset_index(drop=True).equals(sma.reset_index(drop=True)))


class ReplayMatchesBacktestTests(unittest.TestCase):
    def test_replay_reproduces_backtest_trade(self):
        cfg = PatternScoutConfig(atr_period=1, atr_min_periods=1,
                                 opening_body_fraction_min=0.5, manipulation_threshold=0.2,
                                 daily_context=DailyContextConfig(enabled=False))
        data = _john_wick_frame()
        bt = PatternScoutBacktester(cfg).run(data)
        self.assertEqual(len(bt.trades), 1)

        # Feed the same rows through the paper engine.
        broker = PaperBroker(cfg)
        from pattern_scout.paper import PaperTrader
        trader = PaperTrader(cfg, ["X"], broker, on_event=lambda m: None)
        norm = normalize_ohlcv(data, cfg)
        for _, r in norm.iterrows():
            trader.on_bar("X", {"timestamp": r["timestamp"], "open": r["open"], "high": r["high"],
                                "low": r["low"], "close": r["close"], "volume": r["volume"]})
        self.assertEqual(len(broker.trades), 1)
        bt_trade = bt.trades[0]
        paper_trade = broker.trades[0]
        self.assertEqual(paper_trade.signal_type, bt_trade.signal_type)
        self.assertAlmostEqual(paper_trade.entry_price, bt_trade.entry_price, places=6)
        self.assertAlmostEqual(paper_trade.exit_price, bt_trade.exit_price, places=6)
        self.assertAlmostEqual(paper_trade.pnl, bt_trade.pnl, places=4)


class LeverageAndFeeTests(unittest.TestCase):
    def test_liquidation_price_long_short(self):
        self.assertIsNone(liquidation_price("long", 100.0, 1.0, 0.005))
        liq_long = liquidation_price("long", 100.0, 20.0, 0.005)
        liq_short = liquidation_price("short", 100.0, 20.0, 0.005)
        self.assertLess(liq_long, 100.0)
        self.assertGreater(liq_short, 100.0)
        # ~5% away at 20x
        self.assertAlmostEqual(liq_long, 100.0 * (1 - (1 / 20 - 0.005)), places=6)

    def test_margin_cap_and_risk_sizing(self):
        cfg = PatternScoutConfig(risk=RiskConfig(account_size=100, risk_fraction=0.02,
                                                 leverage=20, sizing_mode="risk"))
        qty, margin, notional = size_position(100.0, 3000.0, 30.0, cfg)
        # 2% of 100 = 2 risk / 30 stop = 0.0667 qty
        self.assertAlmostEqual(qty, 2.0 / 30.0, places=6)
        self.assertAlmostEqual(margin, notional / 20.0, places=6)

    def test_auto_leverage_scales_with_stop(self):
        from pattern_scout.paper import effective_leverage
        cfg = PatternScoutConfig(risk=RiskConfig(leverage=100, auto_leverage=True,
                                                 liquidation_safety=1.3, maintenance_margin_rate=0.005))
        wide = effective_leverage(1000.0, 950.0, cfg)    # 5% stop
        tight = effective_leverage(1000.0, 997.0, cfg)   # 0.3% stop
        self.assertLess(wide, 20.0)          # auto-reduced well below the 100x cap
        self.assertGreaterEqual(tight, wide) # tighter stop allows more leverage
        self.assertLessEqual(tight, 100.0)   # never above the cap
        # With auto_leverage off, it always returns the configured cap.
        cfg.risk.auto_leverage = False
        self.assertEqual(effective_leverage(1000.0, 950.0, cfg), 100.0)

    def test_net_pnl_subtracts_fees(self):
        cfg = PatternScoutConfig(
            atr_period=1, atr_min_periods=1, opening_body_fraction_min=0.5,
            manipulation_threshold=0.2, daily_context=DailyContextConfig(enabled=False),
            risk=RiskConfig(account_size=100, risk_fraction=0.02, leverage=20, point_value=1.0,
                            auto_leverage=False),
            execution=ExecutionConfig(taker_fee_pct=0.0006, maker_fee_pct=0.0002),
        )
        data = _john_wick_frame()
        from pattern_scout.paper import PaperTrader
        broker = PaperBroker(cfg)
        trader = PaperTrader(cfg, ["X"], broker, on_event=lambda m: None)
        norm = normalize_ohlcv(data, cfg)
        for _, r in norm.iterrows():
            trader.on_bar("X", {"timestamp": r["timestamp"], "open": r["open"], "high": r["high"],
                                "low": r["low"], "close": r["close"], "volume": r["volume"]})
        self.assertEqual(len(broker.trades), 1)
        t = broker.trades[0]
        self.assertIsNotNone(t.fees)
        self.assertGreater(t.fees, 0.0)
        self.assertAlmostEqual(t.pnl, t.gross_pnl - t.fees, places=6)
        self.assertEqual(t.leverage, 20.0)
        self.assertIsNotNone(t.liquidation_price)


class TrailingStopTests(unittest.TestCase):
    def _engine(self):
        from pattern_scout.config import ExitManagementConfig
        from pattern_scout.paper import PaperBroker, SymbolEngine
        cfg = PatternScoutConfig(exit_management=ExitManagementConfig(
            mode="trailing", initial_stop_atr_fraction=0.25, breakeven_trigger_r=1.0,
            trail_trigger_r=1.0, trail_atr_fraction=0.6, use_fixed_target=False))
        return SymbolEngine("X", cfg, PaperBroker(cfg))

    def _trade(self, side, entry, initial_stop, atr):
        from pattern_scout.paper import PaperTrade
        return PaperTrade(symbol="X", session="s", side=side, signal_type="john_wick",
                          signal_time="t", entry_time="t", exit_time=None, entry_price=entry,
                          exit_price=None, stop_price=initial_stop, target_price=None, quantity=1.0,
                          pnl=None, r_multiple=None, exit_reason=None, atr_fraction=1.0,
                          atr=atr, initial_stop=initial_stop, trail_extreme=entry)

    def test_long_trailing_moves_to_breakeven_then_trails(self):
        eng = self._engine()
        trade = self._trade("long", entry=100.0, initial_stop=95.0, atr=10.0)  # risk = 5
        eng._update_trailing(trade, {"high": 106.0, "low": 101.0, "close": 105.0})  # +1.2R
        self.assertTrue(trade.breakeven_done)
        self.assertGreaterEqual(trade.stop_price, 100.0)  # at least break-even
        eng._update_trailing(trade, {"high": 112.0, "low": 108.0, "close": 111.0})  # +2.4R
        # stop trails to extreme - 0.6*ATR = 112 - 6 = 106
        self.assertAlmostEqual(trade.stop_price, 106.0, places=6)

    def test_trailing_never_loosens_stop(self):
        eng = self._engine()
        trade = self._trade("long", entry=100.0, initial_stop=95.0, atr=10.0)
        eng._update_trailing(trade, {"high": 112.0, "low": 108.0, "close": 111.0})
        high = trade.stop_price
        eng._update_trailing(trade, {"high": 104.0, "low": 103.0, "close": 103.5})  # pullback
        self.assertGreaterEqual(trade.stop_price, high)  # stop must not move down


if __name__ == "__main__":
    unittest.main()
