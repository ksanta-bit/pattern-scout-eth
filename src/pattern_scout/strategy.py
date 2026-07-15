from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import PatternScoutConfig

Side = Literal["long", "short"]


@dataclass(frozen=True)
class SessionSetup:
    session: object
    side: Side
    opening_start: pd.Timestamp
    opening_end: pd.Timestamp
    opening_high: float
    opening_low: float
    opening_open: float
    opening_close: float
    opening_range: float
    opening_body_fraction: float
    atr: float
    atr_fraction: float
    preferred: bool
    daily_context_valid: bool
    daily_context_level: Optional[float]
    daily_context_kind: str
    daily_context_distance_atr: Optional[float]


@dataclass(frozen=True)
class Signal:
    session: object
    side: Side
    signal_type: str
    signal_time: pd.Timestamp
    trigger_price: float
    stop_price: float
    target_price: float
    opening_high: float
    opening_low: float
    atr_fraction: float
    daily_context_level: Optional[float]
    daily_context_kind: str
    daily_context_distance_atr: Optional[float]
    notes: str


def load_ohlcv_csv(path: str | Path, config: PatternScoutConfig) -> pd.DataFrame:
    data = pd.read_csv(path)
    return normalize_ohlcv(data, config)


def normalize_ohlcv(data: pd.DataFrame, config: PatternScoutConfig) -> pd.DataFrame:
    frame = data.copy()
    aliases = {
        "date": "timestamp",
        "datetime": "timestamp",
        "time": "timestamp",
    }
    for source, target in aliases.items():
        if source in frame.columns and target not in frame.columns:
            frame = frame.rename(columns={source: target})

    required = ["timestamp", "open", "high", "low", "close"]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    if "volume" not in frame.columns:
        frame["volume"] = 0.0

    tz = ZoneInfo(config.timezone)
    timestamp = pd.to_datetime(frame["timestamp"], utc=False)
    if getattr(timestamp.dt, "tz", None) is None:
        timestamp = timestamp.dt.tz_localize(tz)
    else:
        timestamp = timestamp.dt.tz_convert(tz)

    frame["timestamp"] = timestamp
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return frame


def annotate_pattern_scout(data: pd.DataFrame, config: PatternScoutConfig) -> pd.DataFrame:
    frame = normalize_ohlcv(data, config) if "session" not in data.columns else data.copy()
    frame = add_session_columns(frame, config)
    frame = add_daily_atr(frame, config)
    frame = add_opening_range_columns(frame, config)
    frame = add_daily_context_columns(frame, config)
    frame = add_candle_shape_columns(frame)
    return frame


def parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def add_session_columns(data: pd.DataFrame, config: PatternScoutConfig) -> pd.DataFrame:
    frame = data.copy()
    session_open = parse_clock(config.session_open)
    session_close = parse_clock(config.session_close)
    local_time = frame["timestamp"].dt.time
    in_session = (local_time >= session_open) & (local_time <= session_close)
    frame = frame.loc[in_session].copy()
    frame["session"] = frame["timestamp"].dt.date
    open_minutes = session_open.hour * 60 + session_open.minute
    minutes = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    frame["minutes_from_open"] = minutes - open_minutes
    frame = frame.loc[frame["minutes_from_open"] >= 0].copy()
    frame["bar_number"] = frame.groupby("session").cumcount()
    return frame.reset_index(drop=True)


def add_daily_atr(data: pd.DataFrame, config: PatternScoutConfig) -> pd.DataFrame:
    frame = data.copy()
    daily = frame.groupby("session").agg(
        daily_high=("high", "max"),
        daily_low=("low", "min"),
        daily_close=("close", "last"),
    )
    prev_close = daily["daily_close"].shift(1)
    tr = pd.concat(
        [
            daily["daily_high"] - daily["daily_low"],
            (daily["daily_high"] - prev_close).abs(),
            (daily["daily_low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    daily["atr_raw"] = tr
    method = getattr(config, "atr_method", "wilder").lower()
    if method in {"wilder", "wilders", "rma", "ema"}:
        # Wilder's smoothing (RMA), exactly as the video shows "ATR (14, WILDERS)".
        # alpha = 1/period. min_periods keeps early sessions usable before a full window.
        atr = tr.ewm(alpha=1.0 / config.atr_period, min_periods=config.atr_min_periods, adjust=False).mean()
    else:
        atr = tr.rolling(config.atr_period, min_periods=config.atr_min_periods).mean()
    # shift(1): a session's ATR only uses data up to and including the previous session (no look-ahead).
    daily["atr"] = atr.shift(1)
    frame = frame.join(daily[["atr"]], on="session")
    return frame


def add_opening_range_columns(data: pd.DataFrame, config: PatternScoutConfig) -> pd.DataFrame:
    frame = data.copy()
    opening_count = config.opening_bars
    opening = frame.loc[frame["bar_number"] < opening_count]
    grouped = opening.groupby("session").agg(
        opening_high=("high", "max"),
        opening_low=("low", "min"),
        opening_open=("open", "first"),
        opening_close=("close", "last"),
        opening_end=("timestamp", "last"),
        opening_count=("timestamp", "count"),
    )
    grouped["opening_range"] = grouped["opening_high"] - grouped["opening_low"]
    grouped["opening_body_fraction"] = (
        (grouped["opening_close"] - grouped["opening_open"]).abs()
        / grouped["opening_range"].replace(0, np.nan)
    )
    frame = frame.join(grouped, on="session")
    frame["atr_fraction"] = frame["opening_range"] / frame["atr"]
    frame["opening_direction"] = np.select(
        [frame["opening_close"] > frame["opening_open"], frame["opening_close"] < frame["opening_open"]],
        ["up", "down"],
        default="flat",
    )
    frame["is_manipulation_session"] = (
        (frame["opening_count"] == opening_count)
        & frame["atr"].notna()
        & (frame["atr"] > 0)
        & (frame["atr_fraction"] >= config.manipulation_threshold)
        & (frame["opening_body_fraction"] >= config.opening_body_fraction_min)
        & (frame["opening_direction"] != "flat")
    )
    return frame


def add_daily_context_columns(data: pd.DataFrame, config: PatternScoutConfig) -> pd.DataFrame:
    frame = data.copy()
    frame["daily_context_valid"] = not config.daily_context.enabled
    frame["daily_context_side"] = ""
    frame["daily_context_level"] = np.nan
    frame["daily_context_kind"] = ""
    frame["daily_context_breakout_session"] = ""
    frame["daily_context_distance"] = np.nan
    frame["daily_context_distance_atr"] = np.nan
    frame["daily_context_tolerance"] = np.nan

    if not config.daily_context.enabled:
        return frame

    daily = frame.groupby("session").agg(
        daily_high=("high", "max"),
        daily_low=("low", "min"),
        daily_close=("close", "last"),
        atr=("atr", "first"),
        opening_high=("opening_high", "first"),
        opening_low=("opening_low", "first"),
        opening_direction=("opening_direction", "first"),
    )
    sessions = list(daily.index)
    cfg = config.daily_context
    for i, session in enumerate(sessions):
        current = daily.iloc[i]
        atr = float(current["atr"]) if pd.notna(current["atr"]) else np.nan
        if not np.isfinite(atr) or atr <= 0:
            continue
        if current["opening_direction"] == "down":
            side: Side = "long"
        elif current["opening_direction"] == "up":
            side = "short"
        else:
            continue

        candidate_start = max(cfg.min_base_sessions, i - cfg.breakout_recent_sessions)
        best: dict | None = None
        for breakout_pos in range(candidate_start, i):
            base_start = max(0, breakout_pos - cfg.lookback_sessions)
            base = daily.iloc[base_start:breakout_pos]
            if len(base) < cfg.min_base_sessions:
                continue
            breakout = daily.iloc[breakout_pos]
            buffer = atr * cfg.breakout_buffer_atr_fraction
            if side == "long":
                level = float(base["daily_high"].max())
                breakout_value = float(breakout["daily_close"] if cfg.require_breakout_close else breakout["daily_high"])
                if breakout_value <= level + buffer:
                    continue
                opening_extreme = float(current["opening_low"])
                kind = "breakout_retest_support"
            else:
                level = float(base["daily_low"].min())
                breakout_value = float(breakout["daily_close"] if cfg.require_breakout_close else breakout["daily_low"])
                if breakout_value >= level - buffer:
                    continue
                opening_extreme = float(current["opening_high"])
                kind = "breakdown_retest_resistance"

            tolerance = max(atr * cfg.retest_tolerance_atr_fraction, abs(level) * cfg.retest_tolerance_pct)
            distance = abs(opening_extreme - level)
            if distance > tolerance:
                continue
            distance_atr = distance / atr
            candidate = {
                "daily_context_valid": True,
                "daily_context_side": side,
                "daily_context_level": level,
                "daily_context_kind": kind,
                "daily_context_breakout_session": str(sessions[breakout_pos]),
                "daily_context_distance": distance,
                "daily_context_distance_atr": distance_atr,
                "daily_context_tolerance": tolerance,
                "rank": (distance_atr, i - breakout_pos),
            }
            if best is None or candidate["rank"] < best["rank"]:
                best = candidate

        if best is None:
            continue
        mask = frame["session"].eq(session)
        for key, value in best.items():
            if key == "rank":
                continue
            frame.loc[mask, key] = value
    return frame


def add_candle_shape_columns(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.copy()
    frame["range"] = frame["high"] - frame["low"]
    frame["body"] = (frame["close"] - frame["open"]).abs()
    frame["body_safe"] = frame["body"].replace(0, np.nan)
    frame["upper_wick"] = frame["high"] - frame[["open", "close"]].max(axis=1)
    frame["lower_wick"] = frame[["open", "close"]].min(axis=1) - frame["low"]
    frame["close_location"] = (frame["close"] - frame["low"]) / frame["range"].replace(0, np.nan)
    return frame


def build_session_setup(session_frame: pd.DataFrame, config: PatternScoutConfig) -> Optional[SessionSetup]:
    first = session_frame.iloc[0]
    if not bool(first["is_manipulation_session"]):
        return None
    if config.daily_context.enabled and not bool(first["daily_context_valid"]):
        return None
    side: Side = "long" if first["opening_direction"] == "down" else "short"
    opening_end = session_frame.loc[session_frame["bar_number"] == config.opening_bars - 1, "timestamp"]
    if opening_end.empty:
        return None
    return SessionSetup(
        session=first["session"],
        side=side,
        opening_start=session_frame.iloc[0]["timestamp"],
        opening_end=opening_end.iloc[0],
        opening_high=float(first["opening_high"]),
        opening_low=float(first["opening_low"]),
        opening_open=float(first["opening_open"]),
        opening_close=float(first["opening_close"]),
        opening_range=float(first["opening_range"]),
        opening_body_fraction=float(first["opening_body_fraction"]),
        atr=float(first["atr"]),
        atr_fraction=float(first["atr_fraction"]),
        preferred=bool(first["atr_fraction"] >= config.preferred_threshold),
        daily_context_valid=bool(first["daily_context_valid"]),
        daily_context_level=float(first["daily_context_level"]) if pd.notna(first["daily_context_level"]) else None,
        daily_context_kind=str(first["daily_context_kind"]),
        daily_context_distance_atr=(
            float(first["daily_context_distance_atr"]) if pd.notna(first["daily_context_distance_atr"]) else None
        ),
    )


def find_session_signals(session_frame: pd.DataFrame, setup: SessionSetup, config: PatternScoutConfig) -> list[Signal]:
    signals: list[Signal] = []
    first_signal_bar = config.opening_bars - 1 if config.allow_third_opening_bar_signal else config.opening_bars
    scan = session_frame.loc[
        (session_frame["bar_number"] >= first_signal_bar)
        & (session_frame["minutes_from_open"] <= config.signal_cutoff_minutes)
    ].copy()
    if scan.empty:
        return signals

    by_pos = {int(row.bar_number): row for row in session_frame.itertuples(index=False)}
    for row in scan.itertuples(index=False):
        pos = int(row.bar_number)
        if config.john_wick.enabled and is_john_wick(row, setup.side, config):
            stop = stop_with_buffer(row.low if setup.side == "long" else row.high, setup.side, config)
            target = setup.opening_high if setup.side == "long" else setup.opening_low
            trigger = row.high if setup.side == "long" else row.low
            if target_is_valid(setup.side, trigger, target, stop):
                signals.append(
                    Signal(
                        session=setup.session,
                        side=setup.side,
                        signal_type="john_wick",
                        signal_time=row.timestamp,
                        trigger_price=float(trigger),
                        stop_price=float(stop),
                        target_price=float(target),
                        opening_high=setup.opening_high,
                        opening_low=setup.opening_low,
                        atr_fraction=setup.atr_fraction,
                        daily_context_level=setup.daily_context_level,
                        daily_context_kind=setup.daily_context_kind,
                        daily_context_distance_atr=setup.daily_context_distance_atr,
                        notes="Break of John Wick extreme after opening manipulation and daily retest context.",
                    )
                )

        if config.power_tower.enabled and pos > 0 and (pos - 1) in by_pos:
            prev = by_pos[pos - 1]
            power_signal = build_power_tower_signal(prev, row, session_frame, setup, config)
            if power_signal is not None:
                signals.append(power_signal)
    return signals


def is_john_wick(row, side: Side, config: PatternScoutConfig) -> bool:
    body = max(float(row.body), 1e-9)
    candle_range = float(row.range)
    if candle_range <= 0:
        return False
    if side == "long":
        return (
            float(row.lower_wick) / body >= config.john_wick.lower_wick_to_body_min
            and float(row.upper_wick) / body <= config.john_wick.upper_wick_to_body_max
            and float(row.close_location) >= config.john_wick.close_location_min_long
        )
    return (
        float(row.upper_wick) / body >= config.john_wick.upper_wick_to_body_min
        and float(row.lower_wick) / body <= config.john_wick.lower_wick_to_body_max
        and float(row.close_location) <= config.john_wick.close_location_max_short
    )


def build_power_tower_signal(prev, row, session_frame: pd.DataFrame, setup: SessionSetup, config: PatternScoutConfig) -> Optional[Signal]:
    prev_range = float(prev.high - prev.low)
    if prev_range <= 0 or setup.atr <= 0:
        return None
    prev_body_fraction = abs(float(prev.close - prev.open)) / prev_range
    if prev_body_fraction < config.power_tower.previous_body_fraction_min:
        return None
    if prev_range / setup.atr < config.power_tower.previous_range_atr_fraction_min:
        return None

    basis = config.power_tower.midpoint_basis
    if basis not in {"range", "body"}:
        raise ValueError("power_tower.midpoint_basis must be 'range' or 'body'")

    if setup.side == "long":
        if not (prev.close < prev.open):
            return None
        threshold = (
            prev.low + prev_range * config.power_tower.midpoint_fraction
            if basis == "range"
            else prev.close + abs(prev.open - prev.close) * config.power_tower.midpoint_fraction
        )
        if row.high < threshold:
            return None
        target = setup.opening_high
        stop_base = session_frame.loc[session_frame["timestamp"] <= row.timestamp, "low"].min()
        stop = stop_with_buffer(stop_base, "long", config)
    else:
        if not (prev.close > prev.open):
            return None
        threshold = (
            prev.high - prev_range * config.power_tower.midpoint_fraction
            if basis == "range"
            else prev.close - abs(prev.close - prev.open) * config.power_tower.midpoint_fraction
        )
        if row.low > threshold:
            return None
        target = setup.opening_low
        stop_base = session_frame.loc[session_frame["timestamp"] <= row.timestamp, "high"].max()
        stop = stop_with_buffer(stop_base, "short", config)

    if not target_is_valid(setup.side, threshold, target, stop):
        return None
    return Signal(
        session=setup.session,
        side=setup.side,
        signal_type="power_tower",
        signal_time=row.timestamp,
        trigger_price=float(threshold),
        stop_price=float(stop),
        target_price=float(target),
        opening_high=setup.opening_high,
        opening_low=setup.opening_low,
        atr_fraction=setup.atr_fraction,
        daily_context_level=setup.daily_context_level,
        daily_context_kind=setup.daily_context_kind,
        daily_context_distance_atr=setup.daily_context_distance_atr,
        notes="50 percent recovery of a large opposite candle after opening manipulation and daily retest context.",
    )


def stop_with_buffer(price: float, side: Side, config: PatternScoutConfig) -> float:
    buffer_pct = config.execution.stop_buffer_pct
    if side == "long":
        return float(price) * (1 - buffer_pct)
    return float(price) * (1 + buffer_pct)


def target_is_valid(side: Side, entry: float, target: float, stop: float) -> bool:
    if side == "long":
        return target > entry > stop
    return target < entry < stop
