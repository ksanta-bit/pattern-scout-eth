from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields, is_dataclass
import json
from pathlib import Path
from typing import Any, Optional


@dataclass
class JohnWickConfig:
    enabled: bool = True
    lower_wick_to_body_min: float = 1.5
    upper_wick_to_body_max: float = 1.0
    close_location_min_long: float = 0.5
    upper_wick_to_body_min: float = 1.5
    lower_wick_to_body_max: float = 1.0
    close_location_max_short: float = 0.5


@dataclass
class PowerTowerConfig:
    enabled: bool = True
    midpoint_fraction: float = 0.5
    midpoint_basis: str = "range"
    previous_body_fraction_min: float = 0.6
    previous_range_atr_fraction_min: float = 0.1


@dataclass
class DailyContextConfig:
    enabled: bool = True
    lookback_sessions: int = 20
    min_base_sessions: int = 3
    breakout_recent_sessions: int = 5
    breakout_buffer_atr_fraction: float = 0.0
    retest_tolerance_atr_fraction: float = 0.15
    retest_tolerance_pct: float = 0.003
    require_breakout_close: bool = True


@dataclass
class ExecutionConfig:
    entry_slippage_pct: float = 0.0
    exit_slippage_pct: float = 0.0
    stop_buffer_pct: float = 0.0
    fee_per_share: float = 0.0
    force_exit_at_session_close: bool = True
    # Percentage-of-notional fees (crypto perps). Bitget USDT-M default:
    # taker 0.06% (0.0006), maker 0.02% (0.0002). Fees are charged on entry and exit.
    taker_fee_pct: float = 0.0
    maker_fee_pct: float = 0.0


@dataclass
class RiskConfig:
    account_size: float = 10_000.0
    risk_fraction: float = 0.01
    fixed_quantity: Optional[float] = None
    point_value: float = 1.0
    # Leverage for margin/perp trading (crypto). 1.0 = spot/no leverage.
    leverage: float = 1.0
    # "risk": size by risk_fraction and stop distance (leverage only caps notional).
    # "leverage": size the position to full notional = equity * leverage (aggressive).
    sizing_mode: str = "risk"
    # Isolated-margin maintenance rate, used to model the liquidation price.
    maintenance_margin_rate: float = 0.005


@dataclass
class PatternScoutConfig:
    timezone: str = "America/New_York"
    session_open: str = "09:30"
    session_close: str = "16:00"
    base_timeframe_minutes: int = 5
    opening_minutes: int = 15
    signal_cutoff_minutes: int = 60
    trigger_cutoff_minutes: int = 60
    atr_period: int = 14
    atr_min_periods: int = 3
    atr_method: str = "wilder"  # "wilder" (as shown in the video: ATR 14, Wilders) or "sma"
    manipulation_threshold: float = 0.2
    preferred_threshold: float = 0.7
    opening_body_fraction_min: float = 0.55
    allow_third_opening_bar_signal: bool = True
    max_trades_per_session: int = 1
    john_wick: JohnWickConfig = field(default_factory=JohnWickConfig)
    power_tower: PowerTowerConfig = field(default_factory=PowerTowerConfig)
    daily_context: DailyContextConfig = field(default_factory=DailyContextConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    @property
    def opening_bars(self) -> int:
        if self.opening_minutes % self.base_timeframe_minutes != 0:
            raise ValueError("opening_minutes must be divisible by base_timeframe_minutes")
        return self.opening_minutes // self.base_timeframe_minutes

    @classmethod
    def from_json(cls, path: str | Path) -> "PatternScoutConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PatternScoutConfig":
        return _build_dataclass(cls, raw)


def _build_dataclass(cls: type, raw: dict[str, Any]):
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        value = raw[f.name]
        default = f.default_factory() if f.default_factory is not MISSING else None
        target_type = f.type
        if isinstance(default, JohnWickConfig):
            kwargs[f.name] = _build_dataclass(JohnWickConfig, value)
        elif isinstance(default, PowerTowerConfig):
            kwargs[f.name] = _build_dataclass(PowerTowerConfig, value)
        elif isinstance(default, DailyContextConfig):
            kwargs[f.name] = _build_dataclass(DailyContextConfig, value)
        elif isinstance(default, ExecutionConfig):
            kwargs[f.name] = _build_dataclass(ExecutionConfig, value)
        elif isinstance(default, RiskConfig):
            kwargs[f.name] = _build_dataclass(RiskConfig, value)
        elif is_dataclass(target_type) and isinstance(value, dict):
            kwargs[f.name] = _build_dataclass(target_type, value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)
