"""Live / replay paper-trading engine for the Pattern Scout strategy.

This module turns the research backtester into something that can run bar-by-bar,
either replaying a CSV (fully offline, for validation) or against a live broker
paper account (Alpaca adapter in ``alpaca.py``).

Design goal: the trading *brain* is exactly the one used by the backtester.
Every closed 5-minute bar we re-annotate the session-so-far and reuse
``build_session_setup`` / ``find_session_signals`` from ``strategy.py``. The only
new logic here is real-time order and position management, which mirrors
``PatternScoutBacktester._simulate_signal`` one bar at a time. That means a
replay run reproduces the backtest trade-for-trade — a strong correctness check.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import PatternScoutConfig
from .strategy import (
    Signal,
    annotate_pattern_scout,
    build_session_setup,
    find_session_signals,
    normalize_ohlcv,
    parse_clock,
)

Bar = dict  # {"timestamp": pd.Timestamp(tz), "open","high","low","close","volume"}


# --------------------------------------------------------------------------- #
# Broker abstraction
# --------------------------------------------------------------------------- #
@dataclass
class PaperTrade:
    symbol: str
    session: str
    side: str
    signal_type: str
    signal_time: str
    entry_time: str
    exit_time: Optional[str]
    entry_price: float
    exit_price: Optional[float]
    stop_price: float
    target_price: float
    quantity: float
    pnl: Optional[float]
    r_multiple: Optional[float]
    exit_reason: Optional[str]
    atr_fraction: float
    status: str = "open"  # open | closed
    leverage: float = 1.0
    margin: Optional[float] = None
    liquidation_price: Optional[float] = None
    notional: Optional[float] = None
    fees: Optional[float] = None
    gross_pnl: Optional[float] = None
    atr: Optional[float] = None
    initial_stop: Optional[float] = None
    trail_extreme: Optional[float] = None
    breakeven_done: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class Broker:
    """Interface. A broker fills entries, manages one position per symbol, and
    reports realised trades. Subclasses: PaperBroker (simulated), AlpacaBroker."""

    def has_open_position(self, symbol: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def open_trade(self, symbol: str, signal: Signal, entry_time, entry_price: float,
                   quantity: float) -> PaperTrade:  # pragma: no cover
        raise NotImplementedError

    def close_trade(self, symbol: str, exit_time, exit_price: float,
                    reason: str) -> Optional[PaperTrade]:  # pragma: no cover
        raise NotImplementedError


class PaperBroker(Broker):
    """Fully simulated broker. Fills exactly where the backtester assumes
    (trigger / stop / target price + configured slippage), so replay == backtest."""

    def __init__(self, config: PatternScoutConfig, starting_equity: Optional[float] = None):
        self.config = config
        self.starting_equity = float(
            starting_equity if starting_equity is not None else config.risk.account_size
        )
        self.equity = self.starting_equity
        self.open_positions: dict[str, PaperTrade] = {}
        self.trades: list[PaperTrade] = []

    def has_open_position(self, symbol: str) -> bool:
        return symbol in self.open_positions

    def _round_trip_fees(self, trade: "PaperTrade", exit_price: float, reason: str) -> float:
        """Entry + exit fees. Percentage-of-notional (crypto perps) plus any
        per-share fee (stocks). Entry is taker (market); target exit is maker
        (resting limit), everything else (stop/liq/session close) is taker."""
        ex = self.config.execution
        taker = ex.taker_fee_pct
        maker = ex.maker_fee_pct
        entry_notional = trade.quantity * trade.entry_price * self.config.risk.point_value
        exit_notional = trade.quantity * exit_price * self.config.risk.point_value
        exit_rate = maker if reason == "target" else taker
        pct_fees = entry_notional * taker + exit_notional * exit_rate
        per_share = trade.quantity * ex.fee_per_share * 2
        return pct_fees + per_share

    def position_size(self, risk_per_unit: float) -> float:
        fixed = self.config.risk.fixed_quantity
        if fixed is not None:
            return float(fixed)
        risk_cash = self.equity * self.config.risk.risk_fraction
        if risk_per_unit <= 0:
            return 0.0
        return risk_cash / risk_per_unit

    def open_trade(self, symbol, signal, entry_time, entry_price, quantity) -> PaperTrade:
        trade = PaperTrade(
            symbol=symbol,
            session=str(signal.session),
            side=signal.side,
            signal_type=signal.signal_type,
            signal_time=_iso(signal.signal_time),
            entry_time=_iso(entry_time),
            exit_time=None,
            entry_price=float(entry_price),
            exit_price=None,
            stop_price=float(signal.stop_price),
            target_price=float(signal.target_price),
            quantity=float(quantity),
            pnl=None,
            r_multiple=None,
            exit_reason=None,
            atr_fraction=float(signal.atr_fraction),
            status="open",
        )
        self.open_positions[symbol] = trade
        return trade

    def close_trade(self, symbol, exit_time, exit_price, reason) -> Optional[PaperTrade]:
        trade = self.open_positions.pop(symbol, None)
        if trade is None:
            return None
        pv = self.config.risk.point_value
        pnl_per_unit = (
            (exit_price - trade.entry_price)
            if trade.side == "long"
            else (trade.entry_price - exit_price)
        )
        gross = pnl_per_unit * trade.quantity * pv
        fees = self._round_trip_fees(trade, exit_price, reason)
        pnl = gross - fees
        risk_per_unit = abs(trade.entry_price - trade.stop_price) * pv
        risk_cash = risk_per_unit * trade.quantity
        trade.exit_time = _iso(exit_time)
        trade.exit_price = float(exit_price)
        trade.fees = float(fees)
        trade.gross_pnl = float(gross)
        trade.pnl = float(pnl)
        trade.r_multiple = float(pnl / risk_cash) if risk_cash else 0.0
        trade.exit_reason = reason
        trade.status = "closed"
        self.equity += pnl
        self.trades.append(trade)
        return trade


# --------------------------------------------------------------------------- #
# Per-symbol state machine
# --------------------------------------------------------------------------- #
@dataclass
class SymbolState:
    session_date: Optional[str] = None
    session_id: Optional[str] = None       # per-window id (supports multiple sessions/day)
    phase: str = "collecting"  # collecting | armed | pending | in_position | done
    trades_done: int = 0
    locked_signal: Optional[Signal] = None
    pending_deadline_min: Optional[int] = None
    consumed_keys: list = field(default_factory=list)


def liquidation_price(side: str, entry: float, leverage: float, mmr: float) -> Optional[float]:
    """Isolated-margin liquidation price. ~entry*(1 -/+ 1/lev) adjusted by the
    maintenance-margin rate. Returns None for spot (leverage <= 1)."""
    if leverage is None or leverage <= 1:
        return None
    frac = (1.0 / leverage) - mmr
    if side == "long":
        return entry * (1.0 - frac)
    return entry * (1.0 + frac)


def effective_leverage(entry: float, stop: float, cfg: PatternScoutConfig) -> float:
    """Effective per-trade leverage. With ``auto_leverage`` the leverage is lowered so
    the liquidation price stays beyond the stop (liq distance >= safety * stop distance),
    which caps the real loss at the intended risk even at 100x nominal."""
    risk = cfg.risk
    max_lev = max(1.0, float(risk.leverage))
    if not getattr(risk, "auto_leverage", False) or entry <= 0:
        return max_lev
    stop_pct = abs(entry - stop) / entry
    if stop_pct <= 0:
        return max_lev
    safe = 1.0 / (risk.liquidation_safety * stop_pct + risk.maintenance_margin_rate)
    return float(max(1.0, min(max_lev, safe)))


def size_position(equity: float, entry_price: float, risk_per_unit: float,
                  cfg: PatternScoutConfig, leverage: Optional[float] = None) -> tuple[float, float, float]:
    """Return (quantity, margin, notional) honouring risk, leverage and margin caps."""
    risk = cfg.risk
    lev = max(1.0, float(leverage if leverage is not None else risk.leverage))
    if risk.fixed_quantity is not None:
        qty = float(risk.fixed_quantity)
    elif risk.sizing_mode == "leverage":
        # Full notional at max leverage (aggressive; risk controlled only by the stop).
        qty = (equity * lev) / entry_price if entry_price > 0 else 0.0
    else:
        # Risk-based: risk_fraction of equity divided by the per-unit stop distance.
        risk_cash = equity * risk.risk_fraction
        qty = (risk_cash / risk_per_unit) if risk_per_unit > 0 else 0.0
    # Cap by available margin: notional cannot exceed equity * leverage.
    max_notional = equity * lev
    notional = qty * entry_price
    if notional > max_notional and entry_price > 0:
        qty = max_notional / entry_price
        notional = qty * entry_price
    margin = notional / lev if lev > 0 else notional
    return qty, margin, notional


def _slip_entry(price: float, side: str, cfg: PatternScoutConfig) -> float:
    pct = cfg.execution.entry_slippage_pct
    return float(price) * (1 + pct if side == "long" else 1 - pct)


def _slip_exit(price: float, side: str, reason: str, cfg: PatternScoutConfig) -> float:
    pct = cfg.execution.exit_slippage_pct
    if pct == 0:
        return float(price)
    # A worse fill in both cases (conservative), matching the backtester.
    return float(price) * (1 - pct if side == "long" else 1 + pct)


class SymbolEngine:
    """Streaming engine for a single instrument. Feed it closed 5m bars via
    ``on_bar``; it emits human-readable events and drives the broker."""

    def __init__(self, symbol: str, config: PatternScoutConfig, broker: Broker,
                 keep_sessions: int = 20, on_event: Optional[Callable[[str], None]] = None):
        self.symbol = symbol
        self.config = config
        self.broker = broker
        self.keep_sessions = keep_sessions
        self.on_event = on_event or (lambda msg: None)
        self.tz = ZoneInfo(config.timezone)
        self.session_close = parse_clock(config.session_close)
        self._rows: list[dict] = []
        self._minute_bars: list[dict] = []  # optional 1m bars for precise stop/target sequencing
        self._cur_mfo: Optional[float] = None  # current bar's minutes-from-window-open
        self.state = SymbolState()

    def set_minute_bars(self, bars: list) -> None:
        """Provide 1-minute candles so open positions resolve stop/target/liq in the
        right order within each 5-minute window. Bars: dicts with timestamp/high/low/close."""
        norm = []
        for b in bars or []:
            row = _normalize_bar(b, self.tz)
            norm.append(row)
        norm.sort(key=lambda r: r["timestamp"])
        self._minute_bars = norm

    def _minute_subbars(self, bar_ts) -> list:
        """1-minute bars whose timestamp falls in [bar_ts, bar_ts + base_timeframe)."""
        if not self._minute_bars:
            return []
        step = pd.Timedelta(minutes=self.config.base_timeframe_minutes)
        end = bar_ts + step
        return [b for b in self._minute_bars if bar_ts <= b["timestamp"] < end]

    @property
    def bars(self) -> pd.DataFrame:
        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        if not self._rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(self._rows, columns=cols)

    # -- helpers ----------------------------------------------------------- #
    def _emit(self, msg: str) -> None:
        self.on_event(f"[{self.symbol}] {msg}")

    def _minutes_from_open(self, ts: pd.Timestamp) -> int:
        so = parse_clock(self.config.session_open)
        return (ts.hour * 60 + ts.minute) - (so.hour * 60 + so.minute)

    def _is_session_close(self, ts: pd.Timestamp) -> bool:
        return ts.time() >= self.session_close

    def _in_any_window(self, ts: pd.Timestamp) -> bool:
        """True if the bar is inside a session window (single or multi-anchor).
        Used to skip the expensive annotate step for bars where nothing can happen."""
        minutes = ts.hour * 60 + ts.minute
        anchors = getattr(self.config, "session_anchors", None)
        if anchors:
            w = int(self.config.session_window_minutes)
            for a in anchors:
                am = parse_clock(a).hour * 60 + parse_clock(a).minute
                if am <= minutes < am + w:
                    return True
            return False
        so = parse_clock(self.config.session_open)
        return so <= ts.time() <= self.session_close

    def _reset_session(self, date_str: str, session_id: Optional[str] = None) -> None:
        self.state = SymbolState(session_date=date_str, session_id=session_id, phase="collecting")

    def seed_history(self, bars: list[Bar]) -> None:
        """Load prior-session bars into the buffer WITHOUT running the strategy.
        Used to warm up ATR / opening-range context cheaply (avoids re-annotating
        the whole window on every historical bar). Only in-session bars are kept."""
        so = parse_clock(self.config.session_open)
        for b in bars:
            row = _normalize_bar(b, self.tz)
            if so <= row["timestamp"].time() <= self.session_close:
                self._rows.append(row)
        self._trim_history()
        # Force a fresh session on the next live bar.
        self.state = SymbolState(session_date=None, session_id=None, phase="collecting")

    def _trim_history(self) -> None:
        if not self._rows:
            return
        keep = sorted({r["timestamp"].date() for r in self._rows})[-self.keep_sessions:]
        keep_set = set(keep)
        self._rows = [r for r in self._rows if r["timestamp"].date() in keep_set]

    # -- main entry point -------------------------------------------------- #
    def on_bar(self, bar: Bar) -> None:
        row = _normalize_bar(bar, self.tz)
        ts = row["timestamp"]
        date_str = str(ts.date())

        # Trim the rolling buffer on a new calendar day (keeps annotate fast).
        if self.state.session_date != date_str:
            self._trim_history()
            self.state.session_date = date_str

        self._rows.append(row)

        # Manage an already-open position on EVERY bar (positions can run past a
        # single session window; they exit on stop/target/trailing or session close).
        if self.state.phase == "in_position":
            if isinstance(self.broker, PaperBroker):
                self._manage_position(row)
            else:
                self._poll_live_exit(row)
            return

        # Fast path: when flat and outside every session window, nothing can trigger —
        # skip the costly annotate (huge speedup with multiple symbols and multi-session).
        if not self._in_any_window(ts):
            return

        # Build the annotated frame and find which session window this bar belongs to.
        try:
            annotated = annotate_pattern_scout(self.bars.copy(), self.config)
        except Exception as exc:  # pragma: no cover - defensive
            self._emit(f"annotate error: {exc}")
            return
        cur = annotated.loc[annotated["timestamp"] == ts]
        if cur.empty:
            return  # bar is outside every session window
        sid = str(cur.iloc[-1]["session"])
        self._cur_mfo = float(cur.iloc[-1]["minutes_from_open"])

        # New session window -> fresh state (each window is an independent opportunity).
        if self.state.session_id != sid:
            self._reset_session(date_str, session_id=sid)

        session_frame = annotated.loc[annotated["session"].astype(str) == sid].copy()
        if session_frame.empty:
            return

        n_bars = len(session_frame)
        opening_bars = self.config.opening_bars

        # Phase: still collecting the opening range.
        if self.state.phase == "collecting":
            if n_bars < opening_bars:
                return
            setup = build_session_setup(session_frame, self.config)
            if setup is None:
                self.state.phase = "done"
                self._emit(f"{sid}: no valid setup (no manipulation / daily context). Standing down.")
                return
            self.state.phase = "armed"
            self._emit(
                f"{sid}: SETUP {setup.side.upper()} | opening [{setup.opening_low:.2f}, "
                f"{setup.opening_high:.2f}] range {setup.opening_range:.2f} "
                f"({setup.atr_fraction*100:.0f}% of ATR {setup.atr:.2f})"
                f"{' PREFERRED' if setup.preferred else ''}. Scanning 5m for John Wick / Power of Tower."
            )
            # fall through so we can also scan on this same bar

        setup = build_session_setup(session_frame, self.config)
        if setup is None:
            return

        # Phase: armed -> look for the first actionable signal.
        if self.state.phase == "armed":
            if self.state.trades_done >= self.config.max_trades_per_session:
                self.state.phase = "done"
                return
            signals = find_session_signals(session_frame, setup, self.config)
            for sig in signals:
                key = f"{sig.signal_type}@{_iso(sig.signal_time)}"
                if key in self.state.consumed_keys:
                    continue
                self.state.locked_signal = sig
                self.state.pending_deadline_min = self.config.trigger_cutoff_minutes
                self.state.phase = "pending"
                self._emit(
                    f"{date_str}: SIGNAL {sig.signal_type} @ {_iso(sig.signal_time)} | "
                    f"trigger {sig.trigger_price:.2f} stop {sig.stop_price:.2f} target {sig.target_price:.2f}"
                )
                break

        # Phase: pending -> wait for the trigger to be crossed intrabar.
        if self.state.phase == "pending":
            self._try_fill(row)

    # -- order / position handling ---------------------------------------- #
    def _try_fill(self, row: dict) -> None:
        sig = self.state.locked_signal
        if sig is None:
            self.state.phase = "armed"
            return
        ts = row["timestamp"]
        mfo = self._cur_mfo if self._cur_mfo is not None else self._minutes_from_open(ts)

        # Respect the trigger cutoff; discard the signal if it expires unfilled.
        if mfo > self.config.trigger_cutoff_minutes:
            self.state.consumed_keys.append(f"{sig.signal_type}@{_iso(sig.signal_time)}")
            self.state.locked_signal = None
            self.state.phase = "armed"
            self._emit(f"signal {sig.signal_type} expired unfilled (past trigger cutoff).")
            return

        # John Wick fills only on a bar strictly after the signal bar (matches backtester).
        if sig.signal_type == "john_wick" and ts <= _to_ts(sig.signal_time, self.tz):
            return

        crossed = (
            (sig.side == "long" and row["high"] >= sig.trigger_price)
            or (sig.side == "short" and row["low"] <= sig.trigger_price)
        )
        if not crossed:
            return

        entry_price = _slip_entry(sig.trigger_price, sig.side, self.config)

        # Optional soft initial stop: widen the stop beyond the wick by k*ATR so a
        # noisy retest right after entry does not fail the trade.
        em = self.config.exit_management
        atr = None
        if sig.atr_fraction:
            atr = (sig.opening_high - sig.opening_low) / sig.atr_fraction
        stop_price = float(sig.stop_price)
        if em.mode == "trailing" and em.initial_stop_atr_fraction > 0 and atr:
            pad = em.initial_stop_atr_fraction * atr
            stop_price = stop_price - pad if sig.side == "long" else stop_price + pad

        risk_per_unit = abs(entry_price - stop_price) * self.config.risk.point_value
        if risk_per_unit <= 0 or not np.isfinite(risk_per_unit):
            self.state.phase = "armed"
            self.state.locked_signal = None
            return
        equity = self.broker.equity if isinstance(self.broker, PaperBroker) else self.config.risk.account_size
        # Leverage as a function of risk: use UP TO the configured leverage, but auto-lower
        # it so the stop always triggers before liquidation (keeps the loss = intended risk).
        lev = effective_leverage(entry_price, stop_price, self.config)
        qty, margin, notional = size_position(equity, entry_price, risk_per_unit, self.config, leverage=lev)
        if qty <= 0:
            self.state.phase = "armed"
            self.state.locked_signal = None
            return
        liq = liquidation_price(sig.side, entry_price, lev, self.config.risk.maintenance_margin_rate)
        trade = self.broker.open_trade(self.symbol, sig, ts, entry_price, qty)
        if trade is not None:
            trade.leverage = lev
            trade.margin = float(margin)
            trade.notional = float(notional)
            trade.liquidation_price = float(liq) if liq is not None else None
            trade.stop_price = float(stop_price)
            trade.initial_stop = float(stop_price)
            trade.atr = float(atr) if atr else None
            trade.trail_extreme = float(entry_price)
            if em.mode == "trailing" and not em.use_fixed_target:
                trade.target_price = None  # let the winner run; exit on the trailing stop
        self.state.phase = "in_position"
        self.state.trades_done += 1
        liq_txt = f" liq {liq:.2f}" if liq is not None else ""
        tgt_txt = f"{trade.target_price:.2f}" if (trade and trade.target_price is not None) else "trailing"
        stop_txt = f"{trade.stop_price:.2f}" if trade else f"{stop_price:.2f}"
        self._emit(
            f"ENTRY {sig.side} {qty:.4f} @ {entry_price:.2f} "
            f"(stop {stop_txt} / target {tgt_txt} | "
            f"{lev:.0f}x margin {margin:.2f}{liq_txt})"
        )
        # Same bar can also hit stop/target; the backtester checks the entry bar too.
        self._manage_position(row)

    def _update_trailing(self, trade, bar: dict) -> None:
        """Move the stop to break-even at +Nr, then trail by k*ATR behind the extreme."""
        em = self.config.exit_management
        if em.mode != "trailing" or not trade.atr or trade.initial_stop is None:
            return
        entry = trade.entry_price
        risk = abs(entry - trade.initial_stop)
        if risk <= 0:
            return
        if trade.side == "long":
            trade.trail_extreme = max(trade.trail_extreme or entry, bar["high"])
            r = (trade.trail_extreme - entry) / risk
            if r >= em.breakeven_trigger_r and not trade.breakeven_done:
                trade.stop_price = max(trade.stop_price, entry)
                trade.breakeven_done = True
            if r >= em.trail_trigger_r:
                trade.stop_price = max(trade.stop_price, trade.trail_extreme - em.trail_atr_fraction * trade.atr)
        else:
            trade.trail_extreme = min(trade.trail_extreme or entry, bar["low"])
            r = (entry - trade.trail_extreme) / risk
            if r >= em.breakeven_trigger_r and not trade.breakeven_done:
                trade.stop_price = min(trade.stop_price, entry)
                trade.breakeven_done = True
            if r >= em.trail_trigger_r:
                trade.stop_price = min(trade.stop_price, trade.trail_extreme + em.trail_atr_fraction * trade.atr)

    def _exit_check(self, bar: dict, trade) -> tuple:
        """Return (exit_price, reason, exit_ts) if this bar hits stop/target/liq, else (None, None, None).
        Uses only high/low, so it works for both 5-minute and 1-minute bars."""
        side = trade.side
        liq = trade.liquidation_price
        tgt = trade.target_price
        if side == "long":
            if liq is not None and bar["low"] <= liq and liq >= trade.stop_price:
                return liq, "liquidation", bar["timestamp"]
            if bar["low"] <= trade.stop_price:
                return _slip_exit(trade.stop_price, side, "stop", self.config), "stop", bar["timestamp"]
            if tgt is not None and bar["high"] >= tgt:
                return _slip_exit(tgt, side, "target", self.config), "target", bar["timestamp"]
        else:
            if liq is not None and bar["high"] >= liq and liq <= trade.stop_price:
                return liq, "liquidation", bar["timestamp"]
            if bar["high"] >= trade.stop_price:
                return _slip_exit(trade.stop_price, side, "stop", self.config), "stop", bar["timestamp"]
            if tgt is not None and bar["low"] <= tgt:
                return _slip_exit(tgt, side, "target", self.config), "target", bar["timestamp"]
        return None, None, None

    def _manage_position(self, row: dict) -> None:
        trade = self.broker.open_positions.get(self.symbol) if isinstance(self.broker, PaperBroker) else None
        sig = self.state.locked_signal
        if trade is None or sig is None:
            return
        ts = row["timestamp"]
        exit_price = reason = exit_ts = None

        # If we have 1-minute candles for this 5-minute window, walk them IN ORDER so
        # that stop vs target vs liquidation is sequenced correctly (matters at high leverage).
        sub = self._minute_subbars(ts)
        if sub:
            for mb in sub:
                self._update_trailing(trade, mb)   # raise the stop as price moves in favor
                exit_price, reason, exit_ts = self._exit_check(mb, trade)
                if exit_price is not None:
                    break
        else:
            self._update_trailing(trade, row)
            exit_price, reason, exit_ts = self._exit_check(row, trade)

        ex = self.config.execution
        if exit_price is None and ex.max_hold_minutes and ex.max_hold_minutes > 0:
            entry_ts = _to_ts(trade.entry_time, self.tz)
            if (ts - entry_ts).total_seconds() / 60.0 >= ex.max_hold_minutes:
                exit_price, reason, exit_ts = float(row["close"]), "max_hold", ts

        if exit_price is None and (
            self._is_session_close(ts) and ex.force_exit_at_session_close
        ):
            exit_price, reason, exit_ts = float(row["close"]), "session_close", ts

        if exit_price is not None:
            closed = self.broker.close_trade(self.symbol, exit_ts or ts, exit_price, reason)
            self.state.phase = "done"
            self.state.locked_signal = None
            if closed:
                self._emit(
                    f"EXIT {reason} @ {exit_price:.2f} | pnl {closed.pnl:+.2f} "
                    f"({closed.r_multiple:+.2f}R) | equity {self._equity():.2f}"
                )

    def _poll_live_exit(self, row: dict) -> None:
        """For a live broker: the bracket order handles stop/target server-side.
        We poll each bar to detect closure and force a flat near the session close."""
        ts = row["timestamp"]
        # Force flat at/after the session close (strategy is intraday-only).
        if self._is_session_close(ts) and self.config.execution.force_exit_at_session_close:
            self.broker.close_trade(self.symbol, ts, float(row["close"]), "session_close")
            self.state.phase = "done"
            self.state.locked_signal = None
            self._emit(f"session close: flattened @ {row['close']:.2f}")
            return
        finalize = getattr(self.broker, "finalize_if_closed", None)
        if finalize is None:
            return
        closed = finalize(self.symbol)
        if closed is not None:
            self.state.phase = "done"
            self.state.locked_signal = None
            self._emit(
                f"bracket exit {closed.exit_reason} @ {closed.exit_price:.2f} | pnl {closed.pnl:+.2f}"
            )

    def force_flat(self, ts, price: float) -> None:
        """Force-close any open position (e.g., end of replay / shutdown)."""
        if isinstance(self.broker, PaperBroker) and self.symbol in self.broker.open_positions:
            self.broker.close_trade(self.symbol, ts, float(price), "forced_flat")

    def _equity(self) -> float:
        return self.broker.equity if isinstance(self.broker, PaperBroker) else float("nan")


# --------------------------------------------------------------------------- #
# Orchestrator + persistence
# --------------------------------------------------------------------------- #
class PaperTrader:
    def __init__(self, config: PatternScoutConfig, symbols: list[str], broker: Broker,
                 state_path: Optional[Path] = None, on_event: Optional[Callable[[str], None]] = None):
        self.config = config
        self.broker = broker
        self.on_event = on_event or (lambda msg: print(msg, flush=True))
        self.state_path = Path(state_path) if state_path else None
        self.engines = {
            sym: SymbolEngine(sym, config, broker, on_event=self.on_event) for sym in symbols
        }

    def on_bar(self, symbol: str, bar: Bar) -> None:
        eng = self.engines.get(symbol)
        if eng is None:
            eng = self.engines[symbol] = SymbolEngine(symbol, self.config, self.broker, on_event=self.on_event)
        eng.on_bar(bar)
        self.persist()

    def persist(self) -> None:
        if not self.state_path or not isinstance(self.broker, PaperBroker):
            return
        payload = {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "starting_equity": self.broker.starting_equity,
            "equity": self.broker.equity,
            "open_positions": {s: t.to_dict() for s, t in self.broker.open_positions.items()},
            "closed_trades": [t.to_dict() for t in self.broker.trades],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def write_reports(self, out_dir: str | Path) -> Path:
        """Write trades/equity/summary CSVs in the same schema as the backtester,
        so the existing ``dashboard`` command renders paper results too."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        trades = self.broker.trades if isinstance(self.broker, PaperBroker) else []
        rows = [t.to_dict() for t in trades]
        trades_df = pd.DataFrame(rows)
        trades_df.to_csv(out / "trades.csv", index=False)
        equity = pd.DataFrame()
        if rows:
            equity["exit_time"] = [r["exit_time"] for r in rows]
            equity["pnl"] = [float(r["pnl"] or 0.0) for r in rows]
            equity["equity"] = equity["pnl"].cumsum()
        equity.to_csv(out / "equity_curve.csv", index=False)
        summary = self.summary()
        summary["total_pnl"] = summary.get("total_pnl", 0.0)
        pd.Series(summary, dtype="object").to_json(out / "summary.json", indent=2)
        # No annotated candles in paper mode; write an empty file for the dashboard.
        (out / "annotated_candles.csv").write_text("timestamp,open,high,low,close,session\n", encoding="utf-8")
        return out

    def summary(self) -> dict:
        if not isinstance(self.broker, PaperBroker):
            return {}
        trades = self.broker.trades
        pnls = [t.pnl for t in trades if t.pnl is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "symbols": list(self.engines.keys()),
            "starting_equity": self.broker.starting_equity,
            "ending_equity": self.broker.equity,
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
            "total_pnl": sum(pnls),
            "profit_factor": (gross_win / gross_loss) if gross_loss else (float("inf") if gross_win else 0.0),
            "avg_r": (sum(t.r_multiple for t in trades if t.r_multiple is not None) / len(trades)) if trades else 0.0,
            "open_positions": len(self.broker.open_positions),
        }


# --------------------------------------------------------------------------- #
# Replay driver (offline validation, no broker/creds needed)
# --------------------------------------------------------------------------- #
def replay_csv(path: str | Path, config: PatternScoutConfig, symbol: str = "REPLAY",
               state_path: Optional[Path] = None,
               on_event: Optional[Callable[[str], None]] = None) -> PaperTrader:
    """Feed a 5-minute CSV through the paper engine one closed bar at a time.
    Produces the same trades a backtest would — the end-to-end sanity check."""
    data = normalize_ohlcv(pd.read_csv(path), config)
    broker = PaperBroker(config)
    trader = PaperTrader(config, [symbol], broker, state_path=state_path, on_event=on_event)
    last_row = None
    for _, r in data.iterrows():
        bar = {
            "timestamp": r["timestamp"],
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r.get("volume", 0.0)),
        }
        trader.on_bar(symbol, bar)
        last_row = bar
    # Leave open positions as-is (a live account would keep them); replay just reports.
    return trader


# --------------------------------------------------------------------------- #
# Live loop (Alpaca paper)
# --------------------------------------------------------------------------- #
def run_live(config: PatternScoutConfig, symbols: list[str], poll_seconds: int = 60,
             state_path: Optional[Path] = None, feed: str = "iex",
             on_event: Optional[Callable[[str], None]] = None,
             once: bool = False) -> "PaperTrader":
    """Live paper-trading loop against an Alpaca paper account.

    Requires ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY in the environment.
    Polls closed 5-minute bars every ``poll_seconds`` and drives the same engine
    used by replay. Runs until the market closes (or one pass if ``once``)."""
    from .alpaca import AlpacaBroker, AlpacaDataFeed, market_is_open  # lazy: stdlib http

    log = on_event or (lambda m: print(m, flush=True))
    broker = AlpacaBroker(config)
    data = AlpacaDataFeed(timeframe="5Min", feed=feed)
    trader = PaperTrader(config, symbols, broker, state_path=state_path, on_event=log)

    log(f"Live paper trading started for {', '.join(symbols)} (feed={feed}). "
        f"Account equity: {broker.equity():.2f}")
    # Warm up: seed each engine with today's (and recent) closed bars so the
    # opening range / ATR context is already in place.
    for sym in symbols:
        for bar in data.latest_bars(sym, lookback_minutes=60 * 24 * 8):
            trader.engines[sym].on_bar(bar)
            data._last_ts[sym] = bar["timestamp"]
    trader.persist()

    while True:
        try:
            open_now = market_is_open()
        except Exception:  # pragma: no cover - network
            open_now = True
        if not open_now:
            log("Market closed. Stopping live loop.")
            break
        for sym in symbols:
            try:
                for bar in data.new_closed_bars(sym):
                    trader.on_bar(sym, bar)
            except Exception as exc:  # pragma: no cover - network
                log(f"[{sym}] poll error: {exc}")
        if once:
            break
        time.sleep(poll_seconds)
    return trader


# --------------------------------------------------------------------------- #
# Live loop (crypto: real data + simulated paper fills)
# --------------------------------------------------------------------------- #
def run_crypto_paper(config: PatternScoutConfig, symbols: list[str], exchange: str = "binance",
                     interval: str = "5m", warmup_days: int = 20,
                     state_path: Optional[Path] = None,
                     on_event: Optional[Callable[[str], None]] = None,
                     feed=None, max_iterations: Optional[int] = None,
                     sleep: bool = True, minute_bars_by_symbol: Optional[dict] = None) -> "PaperTrader":
    """Paper-live on real crypto prices.

    Pulls public candlesticks (no exchange key needed) and fills orders with the
    simulated ``PaperBroker``. Runs 24/7, waking a few seconds after each 5-minute
    bar closes so the strategy always sees the freshest closed candle.

    ``feed`` / ``max_iterations`` / ``sleep`` exist so the loop is testable offline.
    """
    from .crypto import make_feed, seconds_to_next_bar

    log = on_event or (lambda m: print(m, flush=True))
    feed = feed or make_feed(exchange, interval)
    broker = PaperBroker(config)
    trader = PaperTrader(config, symbols, broker, state_path=state_path, on_event=log)

    log(f"Crypto paper-live | exchange={exchange} interval={interval} symbols={', '.join(symbols)} "
        f"| starting equity {broker.starting_equity:.2f}")
    # Provide 1-minute candles (if any) for precise intrabar stop/target sequencing.
    for sym in symbols:
        mb = (minute_bars_by_symbol or {}).get(sym)
        if mb:
            trader.engines[sym].set_minute_bars(mb)

    # Warm up history so ATR + opening range context is ready before the first live bar.
    for sym in symbols:
        try:
            hist = feed.history(sym, days=warmup_days)
        except Exception as exc:  # pragma: no cover - network
            log(f"[{sym}] history error: {exc}")
            hist = []
        eng = trader.engines[sym]
        if hist:
            last_date = max(b["timestamp"].date() for b in hist)
            prior = [b for b in hist if b["timestamp"].date() < last_date]
            todays = [b for b in hist if b["timestamp"].date() == last_date]
            eng.seed_history(prior)          # cheap: no per-bar strategy work
            for bar in todays:               # bounded: only the current session
                eng.on_bar(bar)
        if hasattr(feed, "prime_seen"):
            feed.prime_seen(sym, hist)
        log(f"[{sym}] warmed up with {len(hist)} bars.")
    trader.persist()

    it = 0
    while True:
        if sleep:
            time.sleep(max(1.0, seconds_to_next_bar(interval)))
        for sym in symbols:
            try:
                for bar in feed.new_closed_bars(sym):
                    trader.on_bar(sym, bar)
            except Exception as exc:  # pragma: no cover - network
                log(f"[{sym}] poll error: {exc}")
        it += 1
        if max_iterations is not None and it >= max_iterations:
            break
    return trader


# --------------------------------------------------------------------------- #
# CI single-shot runner (idempotent, for GitHub Actions)
# --------------------------------------------------------------------------- #
def run_crypto_ci(config: PatternScoutConfig, symbols: list[str], out_dir: str | Path,
                  cumulative_path: str | Path, exchange: str = "binance",
                  interval: str = "5m", lookback_days: int = 4, feed=None,
                  on_event: Optional[Callable[[str], None]] = None) -> dict:
    """One deterministic pass for a scheduled/stateless environment (GitHub Actions).

    Each run replays the last ``lookback_days`` of real bars, extracts the trades
    for the sessions in that window, and MERGES them (keyed by symbol+session+signal)
    into a cumulative JSON committed to the repo. Equity is recomputed from the full
    cumulative log, so repeated runs are idempotent and safe to schedule every 5 min.
    """
    _base_log = on_event or (lambda m: print(m, flush=True))
    bot_log: list[str] = []

    def log(m):
        bot_log.append(m)
        _base_log(m)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cpath = Path(cumulative_path)
    cumulative: dict = {"variants": {"off": {"trades": {}, "open": {}},
                                     "on": {"trades": {}, "open": {}}}}
    if cpath.exists():
        try:
            loaded = json.loads(cpath.read_text(encoding="utf-8"))
            if "variants" in loaded:
                cumulative = loaded
            elif loaded.get("trades"):   # migrate old single-variant format
                key = "on" if config.daily_context.enabled else "off"
                cumulative["variants"][key] = {"trades": loaded.get("trades", {}),
                                               "open": loaded.get("open", {})}
        except Exception:
            pass
    cumulative.setdefault("variants", {"off": {"trades": {}, "open": {}},
                                       "on": {"trades": {}, "open": {}}})

    # Shared feed (strategy + 1-minute chart candles).
    if feed is None:
        from .crypto import make_feed
        feed = make_feed(exchange, interval)

    # Shared 1-minute candles (for precise intrabar stop/target and the chart).
    minute_bars = {}
    for sym in symbols:
        try:
            minute_bars[sym] = feed.history(sym, "1m", days=2)
        except Exception as exc:  # pragma: no cover - network
            log(f"[{sym}] 1m history error: {exc}")

    import copy
    variant_summaries = {}
    for vkey, enabled in [("off", False), ("on", True)]:
        cfg_v = copy.deepcopy(config)
        cfg_v.daily_context.enabled = enabled
        prev = cumulative["variants"].get(vkey, {"trades": {}, "open": {}})
        trades_v = dict(prev.get("trades", {}))
        open_v = {}
        for sym in symbols:
            trader = run_crypto_paper(cfg_v, [sym], interval=interval, warmup_days=lookback_days,
                                      feed=feed, max_iterations=1, sleep=False,
                                      on_event=(log if vkey == ("on" if config.daily_context.enabled else "off") else _base_log),
                                      minute_bars_by_symbol=minute_bars)
            for t in trader.broker.trades:
                trades_v[f"{sym}:{t.session}:{t.signal_type}"] = t.to_dict()
            eng = trader.engines.get(sym)
            last_price = float(eng._rows[-1]["close"]) if (eng and eng._rows) else None
            for _, t in trader.broker.open_positions.items():
                d = t.to_dict()
                if last_price is not None:
                    d["current_price"] = last_price
                    pv = cfg_v.risk.point_value
                    per = (last_price - t.entry_price) if t.side == "long" else (t.entry_price - last_price)
                    d["unrealized_pnl"] = float(per * t.quantity * pv)
                    d["unrealized_r"] = float(
                        d["unrealized_pnl"] / (abs(t.entry_price - t.stop_price) * t.quantity * pv)
                    ) if (t.entry_price != t.stop_price) else 0.0
                open_v[f"{sym}:{t.session}:{t.signal_type}"] = d
        cumulative["variants"][vkey] = {"trades": trades_v, "open": open_v}

        closed = [t for t in trades_v.values() if t.get("status") == "closed"]
        closed.sort(key=lambda t: t.get("exit_time") or t.get("entry_time") or "")
        open_positions = list(open_v.values())
        realized = sum(float(t.get("pnl") or 0.0) for t in closed)
        unrealized = sum(float(t.get("unrealized_pnl") or 0.0) for t in open_positions)
        variant_summaries[vkey] = _variant_report(closed, open_positions, realized, unrealized,
                                                   float(config.risk.account_size))

    cumulative["starting_capital"] = float(config.risk.account_size)
    cumulative["updated"] = datetime.now(tz=None).isoformat(timespec="seconds")
    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text(json.dumps(cumulative, indent=2), encoding="utf-8")

    # 1-minute chart candles (primary symbol).
    chart_symbol = symbols[0] if symbols else None
    chart_candles = []
    if chart_symbol is not None:
        try:
            raw = feed.get_klines(chart_symbol, "1m", limit=300)
            chart_candles = [
                {"time": int(b["timestamp"].value // 1_000_000_000),
                 "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]}
                for b in raw
            ]
        except Exception as exc:  # pragma: no cover - network
            log(f"[{chart_symbol}] 1m chart fetch error: {exc}")

    default_variant = "on" if config.daily_context.enabled else "off"
    d = variant_summaries[default_variant]
    log(f"CI pass complete [{default_variant}]. Closed: {len(d['closed'])} | "
        f"open: {len(d['open'])} | equity {d['equity']:.2f}")
    _write_cumulative_reports(out, variant_summaries, default_variant, config,
                              chart_symbol=chart_symbol, chart_candles=chart_candles,
                              bot_log=bot_log[-25:], symbols=list(symbols))
    return cumulative


def _variant_report(closed, open_positions, realized, unrealized, starting):
    pnls = [float(t.get("pnl") or 0.0) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    summary = {
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
        "total_pnl": sum(pnls),
        "avg_r": (sum(float(t.get("r_multiple") or 0.0) for t in closed) / len(closed)) if closed else 0.0,
        "profit_factor": (sum(wins) / gl) if gl else (float("inf") if wins else 0.0),
    }
    return {"closed": closed, "open": open_positions, "equity": starting + realized,
            "unrealized": unrealized, "equity_incl_unrealized": starting + realized + unrealized,
            "summary": summary}


def _write_cumulative_reports(out: Path, variant_summaries: dict, default_variant: str,
                              config: PatternScoutConfig,
                              chart_symbol: Optional[str] = None,
                              chart_candles: Optional[list] = None,
                              bot_log: Optional[list] = None,
                              symbols: Optional[list] = None) -> None:
    starting = float(config.risk.account_size)
    dv = variant_summaries[default_variant]
    rows = dv["closed"]
    # CSV/summary reflect the DEFAULT variant (for external tooling).
    pd.DataFrame(rows).to_csv(out / "trades.csv", index=False)
    equity = pd.DataFrame()
    if rows:
        equity["exit_time"] = [r.get("exit_time") for r in rows]
        equity["pnl"] = [float(r.get("pnl") or 0.0) for r in rows]
        equity["equity"] = starting + equity["pnl"].cumsum()
    equity.to_csv(out / "equity_curve.csv", index=False)
    summ = dict(dv["summary"]); summ["starting_capital"] = starting
    summ["ending_equity"] = dv["equity"]; summ["open_positions"] = len(dv["open"])
    pd.Series(summ, dtype="object").to_json(out / "summary.json", indent=2)

    from .dashboard import build_crypto_dashboard
    dash = build_crypto_dashboard(
        out / "dashboard.html",
        starting_capital=starting,
        variants=variant_summaries,
        default_variant=default_variant,
        chart_symbol=chart_symbol,
        chart_candles=chart_candles or [],
        bot_log=bot_log or [],
        leverage=float(config.risk.leverage),
        repo=os.environ.get("GITHUB_REPOSITORY", ""),
        symbols=symbols or ([chart_symbol] if chart_symbol else []),
    )
    (out / "index.html").write_text(Path(dash).read_text(encoding="utf-8"), encoding="utf-8")


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #
def _normalize_bar(bar: Bar, tz: ZoneInfo) -> dict:
    ts = bar["timestamp"]
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize(tz)
    else:
        ts = ts.tz_convert(tz)
    return {
        "timestamp": ts,
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": float(bar.get("volume", 0.0)),
    }


def _iso(ts) -> str:
    if isinstance(ts, str):
        return ts
    return pd.Timestamp(ts).isoformat()


def _to_ts(value, tz: ZoneInfo) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(tz)
    return ts


def _fallback_size(config: PatternScoutConfig, risk_per_unit: float) -> float:
    fixed = config.risk.fixed_quantity
    if fixed is not None:
        return float(fixed)
    risk_cash = config.risk.account_size * config.risk.risk_fraction
    return risk_cash / risk_per_unit if risk_per_unit > 0 else 0.0
