from dataclasses import dataclass
from datetime import date

from decision_engine.models import TradeDirection
from options.models import StrategyType

from .models import PersistedLeg


@dataclass(frozen=True)
class SROptionPositionState:
    """Options counterpart to stocks.sr_models.SRStockPositionState.
    stop_price/target_price are underlying-price levels (not option premium)
    -- the S/R thesis is about where the underlying is relative to a level,
    the option is just a leveraged way to express it. entry_cost_per_unit is
    the option premium (net debit per contract), used only for P&L, not for
    the exit check."""

    symbol: str
    qty: int
    entry_cost_per_unit: float
    stop_price: float
    target_price: float
    stop_streak: int = 0


@dataclass(frozen=True)
class SROptionPositionRecord:
    symbol: str
    strategy_type: StrategyType
    direction: TradeDirection
    entry_date: date
    legs: list[PersistedLeg]
    state: SROptionPositionState
