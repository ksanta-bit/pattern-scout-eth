from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .config import PatternScoutConfig
from .strategy import (
    Signal,
    annotate_pattern_scout,
    build_session_setup,
    find_session_signals,
    load_ohlcv_csv,
)


@dataclass
class Trade:
    session: object
    side: str
    signal_type: str
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    quantity: float
    pnl: float
    r_multiple: float
    exit_reason: str
    atr_fraction: float
    opening_high: float
    opening_low: float
    daily_context_level: Optional[float]
    daily_context_kind: str
    daily_context_distance_atr: Optional[float]
    notes: str

    def to_dict(self) -> dict:
        raw = asdict(self)
        for key in ["signal_time", "entry_time", "exit_time"]:
            raw[key] = raw[key].isoformat()
        raw["session"] = str(raw["session"])
        return raw


@dataclass
class BacktestResult:
    trades: list[Trade]
    annotated: pd.DataFrame

    @property
    def trades_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([trade.to_dict() for trade in self.trades])

    def summary(self) -> dict:
        trades = self.trades_frame
        if trades.empty:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_r": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
            }
        pnl = trades["pnl"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        equity = pnl.cumsum()
        drawdown = equity - equity.cummax()
        profit_factor = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else float("inf")
        by_signal = trades.groupby("signal_type")["pnl"].agg(["count", "sum", "mean"]).to_dict("index")
        return {
            "total_trades": int(len(trades)),
            "wins": int((pnl > 0).sum()),
            "losses": int((pnl < 0).sum()),
            "win_rate": float((pnl > 0).mean()),
            "total_pnl": float(pnl.sum()),
            "avg_pnl": float(pnl.mean()),
            "avg_r": float(trades["r_multiple"].astype(float).mean()),
            "profit_factor": profit_factor,
            "max_drawdown": float(drawdown.min()),
            "by_signal": by_signal,
        }

    def write_reports(self, output_dir: str | Path) -> None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        trades = self.trades_frame
        trades.to_csv(out / "trades.csv", index=False)
        equity = pd.DataFrame()
        if not trades.empty:
            equity["exit_time"] = trades["exit_time"]
            equity["pnl"] = trades["pnl"].astype(float)
            equity["equity"] = equity["pnl"].cumsum()
        equity.to_csv(out / "equity_curve.csv", index=False)
        summary = pd.Series(self.summary(), dtype="object")
        summary.to_json(out / "summary.json", indent=2)
        self.annotated.to_csv(out / "annotated_candles.csv", index=False)


class PatternScoutBacktester:
    def __init__(self, config: Optional[PatternScoutConfig] = None):
        self.config = config or PatternScoutConfig()

    def run_csv(self, path: str | Path) -> BacktestResult:
        data = load_ohlcv_csv(path, self.config)
        return self.run(data)

    def run(self, data: pd.DataFrame) -> BacktestResult:
        annotated = annotate_pattern_scout(data, self.config)
        trades: list[Trade] = []
        for _, session_frame in annotated.groupby("session", sort=True):
            setup = build_session_setup(session_frame, self.config)
            if setup is None:
                continue
            signals = find_session_signals(session_frame, setup, self.config)
            session_trades = 0
            for signal in signals:
                trade = self._simulate_signal(session_frame, signal)
                if trade is None:
                    continue
                trades.append(trade)
                session_trades += 1
                if session_trades >= self.config.max_trades_per_session:
                    break
        return BacktestResult(trades=trades, annotated=annotated)

    def _simulate_signal(self, session_frame: pd.DataFrame, signal: Signal) -> Optional[Trade]:
        if signal.signal_type == "john_wick":
            time_mask = session_frame["timestamp"] > signal.signal_time
        else:
            time_mask = session_frame["timestamp"] >= signal.signal_time
        candidates = session_frame.loc[
            time_mask & (session_frame["minutes_from_open"] <= self.config.trigger_cutoff_minutes)
        ].copy()
        if candidates.empty:
            return None

        entry_row = None
        raw_entry = signal.trigger_price
        for row in candidates.itertuples(index=False):
            if signal.side == "long" and row.high >= signal.trigger_price:
                entry_row = row
                break
            if signal.side == "short" and row.low <= signal.trigger_price:
                entry_row = row
                break
        if entry_row is None:
            return None

        entry_price = self._apply_entry_slippage(raw_entry, signal.side)
        risk_per_unit = abs(entry_price - signal.stop_price) * self.config.risk.point_value
        if risk_per_unit <= 0 or not np.isfinite(risk_per_unit):
            return None
        quantity = self._position_size(risk_per_unit)

        after_entry = session_frame.loc[session_frame["timestamp"] >= entry_row.timestamp].copy()
        exit_price = float(after_entry.iloc[-1]["close"])
        exit_time = after_entry.iloc[-1]["timestamp"]
        exit_reason = "session_close"

        for row in after_entry.itertuples(index=False):
            if signal.side == "long":
                stop_hit = row.low <= signal.stop_price
                target_hit = row.high >= signal.target_price
                if stop_hit:
                    exit_price = self._apply_exit_slippage(signal.stop_price, signal.side, "stop")
                    exit_time = row.timestamp
                    exit_reason = "stop"
                    break
                if target_hit:
                    exit_price = self._apply_exit_slippage(signal.target_price, signal.side, "target")
                    exit_time = row.timestamp
                    exit_reason = "target"
                    break
            else:
                stop_hit = row.high >= signal.stop_price
                target_hit = row.low <= signal.target_price
                if stop_hit:
                    exit_price = self._apply_exit_slippage(signal.stop_price, signal.side, "stop")
                    exit_time = row.timestamp
                    exit_reason = "stop"
                    break
                if target_hit:
                    exit_price = self._apply_exit_slippage(signal.target_price, signal.side, "target")
                    exit_time = row.timestamp
                    exit_reason = "target"
                    break

        pnl_per_unit = (exit_price - entry_price) if signal.side == "long" else (entry_price - exit_price)
        gross_pnl = pnl_per_unit * quantity * self.config.risk.point_value
        fees = quantity * self.config.execution.fee_per_share * 2
        pnl = gross_pnl - fees
        risk_cash = risk_per_unit * quantity
        r_multiple = pnl / risk_cash if risk_cash else 0.0
        return Trade(
            session=signal.session,
            side=signal.side,
            signal_type=signal.signal_type,
            signal_time=signal.signal_time,
            entry_time=entry_row.timestamp,
            exit_time=exit_time,
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            stop_price=float(signal.stop_price),
            target_price=float(signal.target_price),
            quantity=float(quantity),
            pnl=float(pnl),
            r_multiple=float(r_multiple),
            exit_reason=exit_reason,
            atr_fraction=float(signal.atr_fraction),
            opening_high=float(signal.opening_high),
            opening_low=float(signal.opening_low),
            daily_context_level=signal.daily_context_level,
            daily_context_kind=signal.daily_context_kind,
            daily_context_distance_atr=signal.daily_context_distance_atr,
            notes=signal.notes,
        )

    def _position_size(self, risk_per_unit: float) -> float:
        fixed_quantity = self.config.risk.fixed_quantity
        if fixed_quantity is not None:
            return float(fixed_quantity)
        risk_cash = self.config.risk.account_size * self.config.risk.risk_fraction
        return risk_cash / risk_per_unit

    def _apply_entry_slippage(self, price: float, side: str) -> float:
        pct = self.config.execution.entry_slippage_pct
        return float(price) * (1 + pct if side == "long" else 1 - pct)

    def _apply_exit_slippage(self, price: float, side: str, reason: str) -> float:
        pct = self.config.execution.exit_slippage_pct
        if pct == 0:
            return float(price)
        if reason == "target":
            return float(price) * (1 - pct if side == "long" else 1 + pct)
        return float(price) * (1 - pct if side == "long" else 1 + pct)
