"""Real crypto market-data feeds (Binance primary, Bitget alternative).

Market data on both exchanges is **public** — no API key is required to pull
candlesticks. That is all this bot needs, because execution stays in *paper*
mode (simulated fills via ``PaperBroker``): you trade the strategy on real,
live prices without risking money or connecting an exchange account. When you
later decide to go live, add an order-placing broker; the data side is unchanged.

Only the standard library is used (urllib), so there is nothing extra to install.

Binance klines endpoint (spot):
    GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=1000
    -> [[openTime, o, h, l, c, vol, closeTime, ...], ...]   (times in ms)

Bitget spot candles (v2):
    GET https://api.bitget.com/api/v2/spot/market/candles?symbol=BTCUSDT&granularity=5min&limit=1000
    -> {"code":"00000","data":[[ts, o, h, l, c, baseVol, quoteVol], ...]}   (ts in ms)
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

BINANCE_BASE = "https://api.binance.com"
BINANCE_US_BASE = "https://api.binance.us"
# Public data proxy: same /api/v3 klines, globally reachable (incl. US CI runners
# where api.binance.com is geo-blocked with HTTP 451). Best choice for GitHub Actions.
BINANCE_VISION_BASE = "https://data-api.binance.vision"
BITGET_BASE = "https://api.bitget.com"

# interval label -> milliseconds
_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
       "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class ExchangeError(RuntimeError):
    pass


def _http_get(url: str, timeout: int = 15) -> object:
    req = Request(url, method="GET")
    req.add_header("User-Agent", "pattern-scout/1.0")
    req.add_header("Accept", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:  # pragma: no cover - network
        raise ExchangeError(f"GET {url} -> HTTP {exc.code}: {exc.read().decode('utf-8','replace')[:200]}") from exc
    except URLError as exc:  # pragma: no cover - network
        raise ExchangeError(f"Network error for {url}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Parsers (pure functions -> unit-testable without network)
# --------------------------------------------------------------------------- #
def parse_binance_klines(payload: list) -> list[dict]:
    out = []
    for k in payload:
        out.append({
            "timestamp": pd.Timestamp(int(k[0]), unit="ms", tz="UTC"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": int(k[6]),
        })
    return out


def parse_bitget_candles(payload: dict) -> list[dict]:
    data = payload.get("data", []) if isinstance(payload, dict) else payload
    out = []
    for k in data:
        out.append({
            "timestamp": pd.Timestamp(int(k[0]), unit="ms", tz="UTC"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    # Bitget returns newest-first for some endpoints; normalize to ascending.
    out.sort(key=lambda b: b["timestamp"])
    return out


# --------------------------------------------------------------------------- #
# Feeds
# --------------------------------------------------------------------------- #
class BinanceDataFeed:
    def __init__(self, interval: str = "5m", base_url: str = BINANCE_BASE):
        self.interval = interval
        self.base_url = base_url
        self._last_open_ms: dict[str, int] = {}

    def get_klines(self, symbol: str, interval: Optional[str] = None,
                   limit: int = 1000, start_ms: Optional[int] = None) -> list[dict]:
        interval = interval or self.interval
        params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)}
        if start_ms is not None:
            params["startTime"] = start_ms
        url = f"{self.base_url}/api/v3/klines?{urlencode(params)}"
        return parse_binance_klines(_http_get(url))

    def history(self, symbol: str, interval: Optional[str] = None, days: int = 20) -> list[dict]:
        """Page back ``days`` days of bars (Binance caps at 1000 per request)."""
        interval = interval or self.interval
        step = _MS.get(interval, 300_000)
        now = int(time.time() * 1000)
        start = now - days * 86_400_000
        bars: list[dict] = []
        cursor = start
        while cursor < now:
            chunk = self.get_klines(symbol, interval, limit=1000, start_ms=cursor)
            if not chunk:
                break
            bars.extend(chunk)
            cursor = chunk[-1]["close_time"] + 1
            if len(chunk) < 1000:
                break
        # keep only fully closed bars
        return [b for b in bars if b["close_time"] < now]

    def new_closed_bars(self, symbol: str) -> list[dict]:
        bars = self.get_klines(symbol, self.interval, limit=3)
        now = int(time.time() * 1000)
        closed = [b for b in bars if b["close_time"] < now]
        last = self._last_open_ms.get(symbol)
        fresh = [b for b in closed if last is None or int(b["timestamp"].value // 1_000_000) > last]
        if fresh:
            self._last_open_ms[symbol] = int(fresh[-1]["timestamp"].value // 1_000_000)
        return fresh

    def prime_seen(self, symbol: str, bars: list[dict]) -> None:
        if bars:
            self._last_open_ms[symbol] = int(bars[-1]["timestamp"].value // 1_000_000)


class BitgetDataFeed:
    _GRAN = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
             "1h": "1h", "4h": "4h", "1d": "1day"}

    def __init__(self, interval: str = "5m", base_url: str = BITGET_BASE):
        self.interval = interval
        self.base_url = base_url
        self._last_open_ms: dict[str, int] = {}

    def get_klines(self, symbol: str, interval: Optional[str] = None, limit: int = 1000) -> list[dict]:
        interval = interval or self.interval
        gran = self._GRAN.get(interval, "5min")
        params = {"symbol": symbol.upper(), "granularity": gran, "limit": min(limit, 1000)}
        url = f"{self.base_url}/api/v2/spot/market/candles?{urlencode(params)}"
        return parse_bitget_candles(_http_get(url))

    def history(self, symbol: str, interval: Optional[str] = None, days: int = 20) -> list[dict]:
        # Bitget returns up to `limit` recent candles; 1000 x 5m ~= 3.5 days.
        return self.get_klines(symbol, interval, limit=1000)

    def new_closed_bars(self, symbol: str) -> list[dict]:
        bars = self.get_klines(symbol, self.interval, limit=5)
        step = _MS.get(self.interval, 300_000)
        now = int(time.time() * 1000)
        closed = [b for b in bars if int(b["timestamp"].value // 1_000_000) + step <= now]
        last = self._last_open_ms.get(symbol)
        fresh = [b for b in closed if last is None or int(b["timestamp"].value // 1_000_000) > last]
        if fresh:
            self._last_open_ms[symbol] = int(fresh[-1]["timestamp"].value // 1_000_000)
        return fresh

    def prime_seen(self, symbol: str, bars: list[dict]) -> None:
        if bars:
            self._last_open_ms[symbol] = int(bars[-1]["timestamp"].value // 1_000_000)


def _single_feed(exchange: str, interval: str) -> object:
    ex = exchange.lower().strip()
    if ex in {"binance", "binance-spot"}:
        return BinanceDataFeed(interval)
    if ex in {"binanceus", "binance-us", "binance.us"}:
        return BinanceDataFeed(interval, base_url=BINANCE_US_BASE)
    if ex in {"binancevision", "binance-vision", "vision"}:
        return BinanceDataFeed(interval, base_url=BINANCE_VISION_BASE)
    if ex in {"bitget"}:
        return BitgetDataFeed(interval)
    raise ExchangeError(f"Unknown exchange '{exchange}'. Use binance, binanceus, binancevision or bitget.")


def make_feed(exchange: str, interval: str = "5m") -> object:
    """Build a data feed. A comma-separated list (e.g. 'bitget,binancevision') returns
    a resilient feed that tries each source in order and uses the first that responds —
    so the bot prefers the venue where you trade but never goes dark if it's unreachable."""
    parts = [p for p in exchange.split(",") if p.strip()]
    if len(parts) <= 1:
        return _single_feed(exchange, interval)
    return ResilientFeed([_single_feed(p, interval) for p in parts])


class ResilientFeed:
    def __init__(self, feeds: list):
        self.feeds = feeds
        self.active = None

    def _order(self):
        return ([self.active] if self.active else []) + [f for f in self.feeds if f is not self.active]

    def _try(self, fn):
        errors = []
        for f in self._order():
            try:
                res = fn(f)
                self.active = f
                return res
            except Exception as exc:  # pragma: no cover - network
                errors.append(f"{type(f).__name__}: {exc}")
        raise ExchangeError("all data sources failed -> " + " | ".join(errors))

    def history(self, symbol, interval=None, days=20):
        return self._try(lambda f: f.history(symbol, interval, days) if interval
                         else f.history(symbol, days=days))

    def get_klines(self, symbol, interval=None, limit=1000):
        return self._try(lambda f: f.get_klines(symbol, interval, limit))

    def new_closed_bars(self, symbol):
        return self._try(lambda f: f.new_closed_bars(symbol))

    def prime_seen(self, symbol, bars):
        for f in self.feeds:
            if hasattr(f, "prime_seen"):
                try:
                    f.prime_seen(symbol, bars)
                except Exception:  # pragma: no cover
                    pass


def seconds_to_next_bar(interval: str = "5m", pad_seconds: int = 3) -> float:
    """Seconds until the next interval boundary (+pad), so we poll right after close."""
    step = _MS.get(interval, 300_000) // 1000
    now = time.time()
    return (step - (now % step)) + pad_seconds
