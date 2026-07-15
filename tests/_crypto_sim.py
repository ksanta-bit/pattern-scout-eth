"""Offline end-to-end simulation of the crypto paper loop with a fake feed.
Run: PYTHONPATH=src python3 tests/_crypto_sim.py
Not a unit test (needs no network, but generates synthetic data)."""
import json
import warnings
import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
from pattern_scout.config import PatternScoutConfig
from pattern_scout.paper import run_crypto_paper


def day_bars(date, base, setup=None):
    ts = pd.date_range(f"{date} 00:00", periods=288, freq="5min", tz="UTC")
    rows = []
    price = base
    for i, t in enumerate(ts):
        if setup == "long" and i < 10:
            # 3-bar bearish flush (strong body) -> John Wick hammer -> rally to opening high.
            seq = [
                (base, base + 1, base - 22, base - 20),      # opening bar 0
                (base - 20, base - 19, base - 47, base - 45),  # opening bar 1
                (base - 45, base - 44, base - 74, base - 72),  # opening bar 2 (close near low)
                (base - 72, base - 65, base - 95, base - 68),  # John Wick hammer (long lower wick)
                (base - 68, base - 40, base - 70, base - 45),  # breaks above wick high -> entry
                (base - 45, base - 15, base - 47, base - 18),
                (base - 18, base + 5, base - 20, base + 2),
                (base + 2, base + 6, base + 1, base + 4),      # >= opening high -> target
                (base + 4, base + 8, base + 2, base + 6),
                (base + 6, base + 9, base + 4, base + 7),
            ]
            o, h, l, c = seq[i]
        else:
            drift = np.random.uniform(-2, 2)
            o = price
            c = price + drift
            h = max(o, c) + abs(np.random.uniform(0, 2))
            l = min(o, c) - abs(np.random.uniform(0, 2))
        rows.append({
            "timestamp": t, "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2), "volume": 100,
            "close_time": int(t.value // 1_000_000) + 300000 - 1,
        })
        price = c
    return rows


def build():
    np.random.seed(3)
    allbars = []
    base = 3000.0
    days = pd.bdate_range("2025-06-02", periods=20)
    setup_date = None
    for di, d in enumerate(days):
        ds = d.strftime("%Y-%m-%d")
        setup = "long" if di == 19 else None
        if setup:
            setup_date = ds
        allbars += day_bars(ds, base, setup)
        base += np.random.uniform(-15, 15)
    return allbars, setup_date


class FakeFeed:
    def __init__(self, bars, split_ts):
        self.hist = [b for b in bars if b["timestamp"] < split_ts]
        self.rest = [b for b in bars if b["timestamp"] >= split_ts]
        self.i = 0

    def history(self, symbol, days=20):
        return self.hist

    def prime_seen(self, symbol, bars):
        pass

    def new_closed_bars(self, symbol):
        if self.i >= len(self.rest):
            return []
        b = self.rest[self.i]
        self.i += 1
        return [b]


def main():
    allbars, setup_date = build()
    split = pd.Timestamp(f"{setup_date} 00:00", tz="UTC")
    feed = FakeFeed(allbars, split)
    cfg = PatternScoutConfig.from_json("config.crypto.json")
    events = []
    trader = run_crypto_paper(
        cfg, ["ETHUSDT"], feed=feed, max_iterations=24, sleep=False,
        on_event=lambda m: events.append(m),
    )
    print("--- key events ---")
    for e in events:
        if any(k in e for k in ["SETUP", "SIGNAL", "ENTRY", "EXIT", "warmed"]):
            print(e)
    print("--- summary ---")
    print(json.dumps(trader.summary(), indent=2, default=str))


if __name__ == "__main__":
    main()
