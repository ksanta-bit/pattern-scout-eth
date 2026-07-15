from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

try:
    from freqtrade.persistence import Trade
    from freqtrade.strategy import IStrategy, stoploss_from_absolute
except Exception:  # Allows local syntax checks without freqtrade installed.
    Trade = Any

    class IStrategy:  # type: ignore[no-redef]
        pass

    def stoploss_from_absolute(*args, **kwargs):  # type: ignore[no-redef]
        return 1.0


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if SRC_ROOT.exists() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pattern_scout.config import PatternScoutConfig
from pattern_scout.strategy import annotate_pattern_scout, build_session_setup, find_session_signals


class PatternScoutFreqtradeStrategy(IStrategy):
    """
    Freqtrade adapter for the Pattern Scout research engine.

    This keeps the video rules in one place by importing the local `pattern_scout`
    package. For faithful research, prefer the standalone backtester because it can
    model intrabar trigger/stop/target touches. Freqtrade signals are candle-close
    based, so entries occur on the next candle open in Freqtrade backtests.
    """

    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = True
    startup_candle_count = 300
    process_only_new_candles = True

    minimal_roi = {"0": 10}
    stoploss = -0.99
    use_custom_stoploss = True
    use_exit_signal = True

    # Adjust these for the timezone of the candles passed to Freqtrade.
    pattern_config = PatternScoutConfig(
        timezone="UTC",
        session_open="13:30",
        session_close="20:00",
        atr_min_periods=3,
    )

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe = dataframe.copy()
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["enter_tag"] = ""
        dataframe["ps_stop_price"] = np.nan
        dataframe["ps_target_price"] = np.nan
        dataframe["ps_signal_type"] = ""

        source = dataframe.rename(columns={"date": "timestamp"})
        annotated = annotate_pattern_scout(source, self.pattern_config)

        signal_rows: list[dict[str, Any]] = []
        for _, session_frame in annotated.groupby("session", sort=True):
            setup = build_session_setup(session_frame, self.pattern_config)
            if setup is None:
                continue
            for signal in find_session_signals(session_frame, setup, self.pattern_config):
                if signal.signal_type == "john_wick":
                    time_mask = session_frame["timestamp"] > signal.signal_time
                else:
                    time_mask = session_frame["timestamp"] >= signal.signal_time
                trigger = session_frame.loc[
                    time_mask & (session_frame["minutes_from_open"] <= self.pattern_config.trigger_cutoff_minutes)
                ]
                if signal.side == "long":
                    trigger = trigger.loc[trigger["high"] >= signal.trigger_price]
                else:
                    trigger = trigger.loc[trigger["low"] <= signal.trigger_price]
                if trigger.empty:
                    continue
                row = trigger.iloc[0]
                signal_rows.append(
                    {
                        "date": row["timestamp"],
                        "side": signal.side,
                        "signal_type": signal.signal_type,
                        "stop": signal.stop_price,
                        "target": signal.target_price,
                    }
                )
                break

        for signal in signal_rows:
            mask = dataframe["date"].eq(signal["date"])
            if signal["side"] == "long":
                dataframe.loc[mask, "enter_long"] = 1
            else:
                dataframe.loc[mask, "enter_short"] = 1
            dataframe.loc[mask, "enter_tag"] = f"pattern_scout_{signal['signal_type']}"
            dataframe.loc[mask, "ps_signal_type"] = signal["signal_type"]
            dataframe.loc[mask, "ps_stop_price"] = signal["stop"]
            dataframe.loc[mask, "ps_target_price"] = signal["target"]

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        if not getattr(self, "dp", None):
            return self.stoploss
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        entry_rows = dataframe.loc[dataframe["date"] <= trade.open_date_utc]
        if entry_rows.empty:
            return self.stoploss
        stop_price = entry_rows.iloc[-1].get("ps_stop_price")
        if pd.isna(stop_price):
            return self.stoploss
        return stoploss_from_absolute(
            float(stop_price),
            current_rate=current_rate,
            is_short=getattr(trade, "is_short", False),
            leverage=getattr(trade, "leverage", 1.0),
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        if not getattr(self, "dp", None):
            return None
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        entry_rows = dataframe.loc[dataframe["date"] <= trade.open_date_utc]
        if entry_rows.empty:
            return None
        target = entry_rows.iloc[-1].get("ps_target_price")
        if pd.isna(target):
            return None
        is_short = getattr(trade, "is_short", False)
        if not is_short and current_rate >= float(target):
            return "opening_range_target"
        if is_short and current_rate <= float(target):
            return "opening_range_target"
        return None
