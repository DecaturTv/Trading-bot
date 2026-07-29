from dataclasses import dataclass, field
from datetime import datetime

from decision_engine.models import TradeDirection


@dataclass(frozen=True)
class SimulatedForexTrade:
    pair: str
    direction: TradeDirection
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    # Realized P&L in multiples of the initial stop distance -- currency-
    # conversion-agnostic (see ForexBacktestEngine docstring for why).
    r_multiple: float
    pnl: float  # r_multiple * risk_dollars fixed at entry
    exit_reason: str  # "stop_loss" | "take_profit" | "end_of_data"


@dataclass(frozen=True)
class ForexBacktestConfig:
    starting_equity: float
    confidence_threshold: float
    risk_pct_per_trade: float
    stop_atr_multiplier: float
    take_profit_r_multiple: float
    atr_period: int = 14
    min_candles_for_signal: int = 30
    warmup_bars: int = 60

    def __post_init__(self):
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        if not 0 <= self.confidence_threshold <= 100:
            raise ValueError("confidence_threshold must be in [0, 100]")
        if not 0 < self.risk_pct_per_trade <= 1:
            raise ValueError("risk_pct_per_trade must be in (0, 1]")
        if self.stop_atr_multiplier <= 0:
            raise ValueError("stop_atr_multiplier must be positive")
        if self.take_profit_r_multiple <= 0:
            raise ValueError("take_profit_r_multiple must be positive")
        if self.atr_period < 1:
            raise ValueError("atr_period must be >= 1")
        if self.min_candles_for_signal < 1:
            raise ValueError("min_candles_for_signal must be >= 1")
        if self.warmup_bars < 1:
            raise ValueError("warmup_bars must be >= 1")


@dataclass(frozen=True)
class ForexBacktestResult:
    pair: str
    trades: list[SimulatedForexTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    starting_equity: float = 0.0
    ending_equity: float = 0.0
