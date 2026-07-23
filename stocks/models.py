from dataclasses import dataclass
from datetime import date

from decision_engine.models import TradeDirection
from trade_management.models import PositionState


@dataclass(frozen=True)
class OpenStockPositionRecord:
    """A tracked direct equity position — a second, independent vehicle
    alongside options (see OpenPositionRecord) for the same universe/signal.
    No legs/strategy_type/expiration since it's a single instrument, not a
    multi-leg strategy: reuses trade_management's PositionState/
    TradeManagementConfig/evaluate_exit as-is, with entry_cost_per_unit and
    current_value_per_unit simply being share prices instead of option
    premiums. Long-only for now (direction is always BULLISH) — bearish
    signals are covered by the options side's long_put instead of shorting
    shares, which would need margin/borrow handling this doesn't have.
    """

    symbol: str
    direction: TradeDirection
    entry_date: date
    state: PositionState
