from dataclasses import dataclass, field
from datetime import date

from decision_engine.models import TradeDirection
from options.models import StrategyType


@dataclass(frozen=True)
class SimulatedTrade:
    symbol: str
    strategy_type: StrategyType
    direction: TradeDirection
    entry_date: date
    exit_date: date
    entry_cost_per_unit: float
    exit_value_per_unit: float
    qty: int
    exit_reason: str  # an ExitAction value, or "end_of_data" for a forced mark-to-market close
    pnl: float


@dataclass(frozen=True)
class BacktestConfig:
    starting_equity: float
    confidence_threshold: float
    target_delta: float  # e.g. 0.40 for a moderately-OTM directional play
    target_dte: int  # in trading days
    volatility_lookback: int = 20
    risk_free_rate: float = 0.0
    warmup_bars: int = 60

    def __post_init__(self):
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        if not 0 <= self.confidence_threshold <= 100:
            raise ValueError("confidence_threshold must be in [0, 100]")
        if not 0 < self.target_delta <= 1:
            raise ValueError("target_delta must be in (0, 1]")
        if self.target_dte <= 0:
            raise ValueError("target_dte must be positive")
        if self.warmup_bars < 1:
            raise ValueError("warmup_bars must be >= 1")


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    trades: list[SimulatedTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    starting_equity: float = 0.0
    ending_equity: float = 0.0
