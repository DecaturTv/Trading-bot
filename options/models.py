from dataclasses import dataclass
from enum import Enum

from broker.models import OptionContract, OrderSide


class StrategyType(str, Enum):
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    DEBIT_SPREAD_VERTICAL = "debit_spread_vertical"
    DEBIT_SPREAD_DIAGONAL = "debit_spread_diagonal"


class StrategyConstructionError(ValueError):
    pass


@dataclass(frozen=True)
class OptionLeg:
    contract: OptionContract
    side: OrderSide


@dataclass(frozen=True)
class OptionStrategy:
    strategy_type: StrategyType
    legs: list[OptionLeg]
    net_debit: float  # total premium paid to open, in dollars (100x multiplier applied)
    max_loss: float
    max_gain: float | None  # None = unbounded (long call) or not closed-form (diagonal)
    net_delta: float
