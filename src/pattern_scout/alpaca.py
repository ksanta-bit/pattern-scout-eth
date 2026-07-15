"""Alpaca paper-trading adapter (standard library only, no extra pip installs).

Used by ``cli.py`` for ``paper-live``. It talks to Alpaca's **paper** endpoints:
- trading:  https://paper-api.alpaca.markets
- data:     https://data.alpaca.markets

Credentials are read from the environment so keys never live in the repo:
    ALPACA_API_KEY_ID
    ALPACA_API_SECRET_KEY

Nothing here trades real money: the base URL is the paper account. Even so, the
engine never sizes beyond the configured risk fraction, and every entry is sent
as a **bracket order** so the stop-loss and take-profit live server-side.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .config import PatternScoutConfig
from .paper import Broker, PaperTrade, _fallback_size, _iso
from .strategy import Signal

TRADING_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise AlpacaError(
            "Missing Alpaca paper credentials. Set them first:\n"
            "  export ALPACA_API_KEY_ID=...\n"
            "  export ALPACA_API_SECRET_KEY=...\n"
            "Create a free paper key at https://app.alpaca.markets (Paper Trading)."
        )
    return key, secret


def _request(method: str, url: str, body: Optional[dict] = None) -> dict:
    key, secret = _credentials()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, method=method)
    req.add_header("APCA-API-KEY-ID", key)
    req.add_header("APCA-API-SECRET-KEY", secret)
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:  # pragma: no cover - network
        detail = exc.read().decode("utf-8", errors="replace")
        raise AlpacaError(f"Alpaca {method} {url} -> HTTP {exc.code}: {detail}") from exc
    except URLError as exc:  # pragma: no cover - network
        raise AlpacaError(f"Alpaca network error for {url}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Broker
# --------------------------------------------------------------------------- #
class AlpacaBroker(Broker):
    def __init__(self, config: PatternScoutConfig):
        self.config = config
        self.trades: list[PaperTrade] = []
        self._open: dict[str, PaperTrade] = {}

    # -- account / positions ---------------------------------------------- #
    def account(self) -> dict:
        return _request("GET", f"{TRADING_BASE}/v2/account")

    def equity(self) -> float:
        try:
            return float(self.account().get("equity", self.config.risk.account_size))
        except AlpacaError:
            return float(self.config.risk.account_size)

    def list_positions(self) -> list[dict]:
        return _request("GET", f"{TRADING_BASE}/v2/positions")

    def has_open_position(self, symbol: str) -> bool:
        try:
            _request("GET", f"{TRADING_BASE}/v2/positions/{symbol}")
            return True
        except AlpacaError:
            return False

    def position_size(self, risk_per_unit: float) -> float:
        fixed = self.config.risk.fixed_quantity
        if fixed is not None:
            return float(fixed)
        eq = self.equity()
        risk_cash = eq * self.config.risk.risk_fraction
        if risk_per_unit <= 0:
            return 0.0
        # Whole shares for equities.
        return float(int(risk_cash / risk_per_unit))

    # -- orders ------------------------------------------------------------ #
    def open_trade(self, symbol, signal: Signal, entry_time, entry_price, quantity) -> PaperTrade:
        qty = int(quantity)
        if qty <= 0:
            raise AlpacaError(f"Computed non-positive quantity for {symbol}.")
        side = "buy" if signal.side == "long" else "sell"
        order = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": round(float(signal.target_price), 2)},
            "stop_loss": {"stop_price": round(float(signal.stop_price), 2)},
        }
        resp = _request("POST", f"{TRADING_BASE}/v2/orders", order)
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
            quantity=float(qty),
            pnl=None,
            r_multiple=None,
            exit_reason=None,
            atr_fraction=float(signal.atr_fraction),
            status="open",
        )
        trade_meta = {"order_id": resp.get("id")}
        self._open[symbol] = trade
        return trade

    def close_trade(self, symbol, exit_time, exit_price, reason) -> Optional[PaperTrade]:
        # Bracket order manages the exit server-side; here we just liquidate if asked
        # (e.g. forced session-close flat) and finalize the local record.
        trade = self._open.pop(symbol, None)
        try:
            _request("DELETE", f"{TRADING_BASE}/v2/positions/{symbol}")
        except AlpacaError:
            pass
        if trade is None:
            return None
        pv = self.config.risk.point_value
        pnl_per_unit = (exit_price - trade.entry_price) if trade.side == "long" else (trade.entry_price - exit_price)
        trade.exit_time = _iso(exit_time)
        trade.exit_price = float(exit_price)
        trade.pnl = float(pnl_per_unit * trade.quantity * pv)
        risk = abs(trade.entry_price - trade.stop_price) * trade.quantity * pv
        trade.r_multiple = float(trade.pnl / risk) if risk else 0.0
        trade.exit_reason = reason
        trade.status = "closed"
        self.trades.append(trade)
        return trade

    def finalize_if_closed(self, symbol: str) -> Optional[PaperTrade]:
        """If the bracket order has closed the position server-side, finalize the
        local trade record using the exit leg's actual filled price. Best-effort."""
        trade = self._open.get(symbol)
        if trade is None or self.has_open_position(symbol):
            return None
        exit_side = "sell" if trade.side == "long" else "buy"
        exit_price = trade.target_price  # fallback
        reason = "bracket"
        try:
            query = urlencode({"status": "closed", "symbols": symbol, "limit": 20, "direction": "desc"})
            orders = _request("GET", f"{TRADING_BASE}/v2/orders?{query}")
            for o in orders:
                if o.get("side") == exit_side and o.get("filled_avg_price"):
                    exit_price = float(o["filled_avg_price"])
                    otype = o.get("type", "")
                    reason = "target" if otype in {"limit"} else ("stop" if "stop" in otype else "bracket")
                    break
        except AlpacaError:
            pass
        return self.close_trade(symbol, datetime.now(timezone.utc), exit_price, reason)

    @property
    def open_positions(self) -> dict:
        return self._open


# --------------------------------------------------------------------------- #
# Data feed
# --------------------------------------------------------------------------- #
class AlpacaDataFeed:
    """Polls closed 5-minute bars for a set of symbols from Alpaca market data."""

    def __init__(self, timeframe: str = "5Min", feed: str = "iex"):
        self.timeframe = timeframe
        self.feed = feed  # "iex" is free; "sip" needs a paid data plan.
        self._last_ts: dict[str, pd.Timestamp] = {}

    def latest_bars(self, symbol: str, lookback_minutes: int = 600) -> list[dict]:
        start = (datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)).isoformat()
        query = urlencode({
            "timeframe": self.timeframe,
            "start": start,
            "feed": self.feed,
            "limit": 1000,
            "adjustment": "raw",
        })
        url = f"{DATA_BASE}/v2/stocks/{symbol}/bars?{query}"
        payload = _request("GET", url)
        out = []
        for b in payload.get("bars", []) or []:
            out.append({
                "timestamp": pd.Timestamp(b["t"]),
                "open": float(b["o"]),
                "high": float(b["h"]),
                "low": float(b["l"]),
                "close": float(b["c"]),
                "volume": float(b.get("v", 0.0)),
            })
        return out

    def new_closed_bars(self, symbol: str) -> list[dict]:
        """Return only bars not seen before (dedup by timestamp)."""
        bars = self.latest_bars(symbol)
        last = self._last_ts.get(symbol)
        fresh = [b for b in bars if last is None or b["timestamp"] > last]
        if fresh:
            self._last_ts[symbol] = fresh[-1]["timestamp"]
        return fresh


def market_is_open() -> bool:
    try:
        clock = _request("GET", f"{TRADING_BASE}/v2/clock")
        return bool(clock.get("is_open", False))
    except AlpacaError:
        return False
