from dataclasses import dataclass
from datetime import date
from enum import Enum

from broker.models import OptionRight, OrderSide
from decision_engine.models import TradeDirection
from options.models import StrategyType


@dataclass(frozen=True)
class TradeManagementConfig:
    """No defaults on purpose: these are business-rule decisions (confirmed
    with the user for this project — see project memory), not engineering
    defaults, so callers must set them explicitly rather than inherit a
    silently-assumed number.
    """

    stop_loss_pct: float  # e.g. 0.50 = close at -50% of premium paid
    profit_target_pct: float  # e.g. 1.00 = scale out at +100% unrealized gain
    scale_out_fraction: float  # e.g. 0.50 = close half the position at the profit target
    trailing_stop_pct: float  # pullback from peak gain % that closes the remainder after scale-out
    min_trading_days_before_expiry: int  # force-close this many trading days before expiration

    def __post_init__(self):
        for name in ("stop_loss_pct", "profit_target_pct", "trailing_stop_pct"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.scale_out_fraction <= 1:
            raise ValueError("scale_out_fraction must be in (0, 1]")
        if self.min_trading_days_before_expiry < 0:
            raise ValueError("min_trading_days_before_expiry must be >= 0")


@dataclass(frozen=True)
class PositionState:
    """Tracks one open strategy's lifecycle for exit decisions."""

    symbol: str  # underlying symbol
    qty: int  # remaining open spread units/contracts
    entry_cost_per_unit: float  # net debit paid per unit, in dollars (100x multiplier, same scale as OptionStrategy.net_debit)
    scaled_out: bool = False
    peak_gain_pct: float = 0.0  # highest unrealized gain % observed since entry


@dataclass(frozen=True)
class PersistedLeg:
    """A single leg's structural details, as needed to re-fetch quotes and
    build a close order after a process restart (a fresh OptionContract
    carries live bid/ask; this only needs to carry what identifies it)."""

    symbol: str  # OCC option symbol
    strike: float
    expiration: date
    right: OptionRight
    side: OrderSide


@dataclass(frozen=True)
class OpenPositionRecord:
    """The full persisted record of one open live position — PositionState
    plus the leg/strategy detail needed to resume tracking it after a
    restart, which PositionState alone doesn't carry."""

    symbol: str  # underlying
    strategy_type: StrategyType
    direction: TradeDirection
    entry_date: date
    legs: list[PersistedLeg]
    state: PositionState


class ExitAction(str, Enum):
    NONE = "none"
    STOP_LOSS = "stop_loss"
    SCALE_OUT = "scale_out"
    TRAILING_STOP = "trailing_stop"
    EXPIRY_EXIT = "expiry_exit"


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction
    qty_to_close: int
    reason: str
