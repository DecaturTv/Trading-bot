from dataclasses import dataclass

from broker.models import Order
from options.models import StrategyType


@dataclass(frozen=True)
class ExecutionResult:
    order: Order
    strategy_type: StrategyType
    symbol: str  # underlying symbol
