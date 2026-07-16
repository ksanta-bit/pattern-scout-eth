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
class ExitManagementConfig:
    # "fixed" = video (stop alla wick, target sul lato opposto dell'opening range).
    # "trailing" = stop iniziale morbido -> break-even -> trailing, target lasciato correre.
    mode: str = "fixed"
    # Allarga lo stop oltre la wick di k*ATR daily, così l'entrata non fallisce sul rumore.
    initial_stop_atr_fraction: float = 0.0
    # A +N*R sposta lo stop a break-even (entrata), eliminando il rischio iniziale.
    breakeven_trigger_r: float = 1.0
    # Da +N*R inizia il trailing.
    trail_trigger_r: float = 1.0
    # Distanza del trailing = k*ATR daily sotto il massimo (long) / sopra il minimo (short).
    trail_atr_fraction: float = 0.5
    # Se true tiene anche un target fisso (lato opposto opening range); se false lascia correre.
    use_fixed_target: bool = True


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
    # Hard cap on how long a position can stay open (minutes). 0 = disabled.
    # Prevents an intraday scalp from sitting open for many hours.
    max_hold_minutes: int = 0


@dataclass
class RiskConfig:
    account_size: float = 10_000.0
    risk_fraction: float = 0.01
    fixed_quantity: Optional[float] = None
    point_value: float = 1.0
    # Max leverage for margin/perp trading (crypto). 1.0 = spot/no leverage. Up to 100x.
    leverage: float = 1.0
    # "risk": size by risk_fraction and stop distance (leverage caps notional/margin).
    # "leverage": size the position to full notional = equity * leverage (aggressive).
    sizing_mode: str = "risk"
    # Isolated-margin maintenance rate, used to model the liquidation price.
    maintenance_margin_rate: float = 0.005
    # Risk management AS A FUNCTION OF LEVERAGE: when true the bot uses UP TO `leverage`
    # but automatically lowers the effective leverage per trade so the stop always
    # triggers before liquidation (liq distance >= liquidation_safety * stop distance).
    auto_leverage: bool = True
    liquidation_safety: float = 1.3
    # Compounding: size positions on the CURRENT equity (capital + realized PnL) instead
    # of the fixed starting capital, so the whole realized capital is reinvested.
    compound: bool = False


@dataclass
class PatternScoutConfig:
    timezone: str = "America/New_York"
    session_open: str = "09:30"
    session_close: str = "16:00"
    # Multi-session (crypto 24/7): list of daily anchor times, each opening a new
    # "session" (opening range + signal window). Empty = classic single session.
    session_anchors: list = field(default_factory=list)
    session_window_minutes: int = 90
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
    exit_management: ExitManagementConfig = field(default_factory=ExitManagementConfig)
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
        elif isinstance(default, ExitManagementConfig):
            kwargs[f.name] = _build_dataclass(ExitManagementConfig, value)
        elif isinstance(default, RiskConfig):
            kwargs[f.name] = _build_dataclass(RiskConfig, value)
        elif is_dataclass(target_type) and isinstance(value, dict):
            kwargs[f.name] = _build_dataclass(target_type, value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)
