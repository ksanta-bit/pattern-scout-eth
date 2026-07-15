"""Parameter optimizer for the Pattern Scout strategy.

Grid-searches the parameters the video leaves undefined (wick ratios, ATR
thresholds, retest tolerance, ...) over one or more 5-minute CSVs using the
fast backtester, then ranks candidates by a robust objective and writes the
best configuration to JSON — ready to feed into ``paper-replay`` / ``paper-live``.

Objective (in order): enough trades to be meaningful, then profit factor, then
average R. This deliberately avoids overfitting to a single lucky trade.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from itertools import product
import json
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .backtester import PatternScoutBacktester
from .config import PatternScoutConfig
from .strategy import load_ohlcv_csv


# Sensible search space. Keep it small so the search stays fast and honest.
DEFAULT_GRID = {
    "manipulation_threshold": [0.15, 0.20, 0.30, 0.50],
    "opening_body_fraction_min": [0.0, 0.45, 0.55, 0.65],
    "preferred_threshold": [0.7],
    "john_wick.lower_wick_to_body_min": [1.0, 1.5, 2.0],
    "power_tower.previous_body_fraction_min": [0.5, 0.6, 0.7],
    "daily_context.enabled": [True, False],
    "daily_context.retest_tolerance_atr_fraction": [0.15, 0.30],
}


def _set_path(cfg_dict: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    node = cfg_dict
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


def _config_to_dict(config: PatternScoutConfig) -> dict:
    return asdict(config)


def load_frames(paths: Iterable[str | Path], config: PatternScoutConfig) -> list[pd.DataFrame]:
    return [load_ohlcv_csv(p, config) for p in paths]


def score(summary: dict, min_trades: int) -> tuple:
    trades = summary.get("total_trades", 0)
    if trades < min_trades:
        return (0, float("-inf"), float("-inf"))
    pf = summary.get("profit_factor", 0.0)
    pf = 1e9 if pf == float("inf") else float(pf)
    return (1, pf, float(summary.get("avg_r", 0.0)))


def evaluate(config: PatternScoutConfig, frames: list[pd.DataFrame]) -> dict:
    total = {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
             "gross_win": 0.0, "gross_loss": 0.0, "r_sum": 0.0}
    for frame in frames:
        result = PatternScoutBacktester(config).run(frame)
        tf = result.trades_frame
        if tf.empty:
            continue
        pnl = tf["pnl"].astype(float)
        total["total_trades"] += int(len(tf))
        total["wins"] += int((pnl > 0).sum())
        total["losses"] += int((pnl < 0).sum())
        total["total_pnl"] += float(pnl.sum())
        total["gross_win"] += float(pnl[pnl > 0].sum())
        total["gross_loss"] += float(abs(pnl[pnl < 0].sum()))
        total["r_sum"] += float(tf["r_multiple"].astype(float).sum())
    n = total["total_trades"]
    pf = (total["gross_win"] / total["gross_loss"]) if total["gross_loss"] > 0 else (
        float("inf") if total["gross_win"] > 0 else 0.0)
    return {
        "total_trades": n,
        "wins": total["wins"],
        "losses": total["losses"],
        "win_rate": (total["wins"] / n) if n else 0.0,
        "total_pnl": total["total_pnl"],
        "avg_r": (total["r_sum"] / n) if n else 0.0,
        "profit_factor": pf,
    }


def grid_search(paths: list[str | Path], base_config: Optional[PatternScoutConfig] = None,
                grid: Optional[dict] = None, min_trades: int = 5,
                on_event=None) -> dict:
    base = base_config or PatternScoutConfig()
    grid = grid or DEFAULT_GRID
    log = on_event or (lambda m: print(m, flush=True))
    frames = load_frames(paths, base)

    keys = list(grid.keys())
    combos = list(product(*[grid[k] for k in keys]))
    log(f"Optimizing over {len(combos)} combinations across {len(frames)} dataset(s)...")

    base_dict = _config_to_dict(base)
    best = None
    results = []
    for i, combo in enumerate(combos, 1):
        cfg_dict = deepcopy(base_dict)
        for k, v in zip(keys, combo):
            _set_path(cfg_dict, k, v)
        config = PatternScoutConfig.from_dict(cfg_dict)
        summary = evaluate(config, frames)
        s = score(summary, min_trades)
        row = {"params": dict(zip(keys, combo)), "summary": summary, "score": s}
        results.append(row)
        if best is None or s > best["score"]:
            best = row
            log(f"[{i}/{len(combos)}] new best PF={summary['profit_factor']:.2f} "
                f"avgR={summary['avg_r']:.2f} trades={summary['total_trades']} :: "
                + ", ".join(f"{k}={v}" for k, v in zip(keys, combo)))

    results.sort(key=lambda r: r["score"], reverse=True)
    best_config_dict = deepcopy(base_dict)
    if best:
        for k, v in best["params"].items():
            _set_path(best_config_dict, k, v)
    return {
        "best_params": best["params"] if best else {},
        "best_summary": best["summary"] if best else {},
        "best_config": best_config_dict,
        "top": results[:10],
        "combinations": len(combos),
    }


def write_best_config(result: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result["best_config"], indent=2), encoding="utf-8")
    return out
