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

    stop_loss_pct: float  # e.g. 0.25 = close at -25% of premium paid
    profit_target_dollars: float  # e.g. 20.0 = scale out scale_out_fraction of the position once unrealized gain reaches $20
    trailing_stop_pct: float  # pullback from peak gain % that closes the remainder after scale-out
    min_trading_days_before_expiry: int  # force-close this many trading days before expiration
    stop_loss_confirmation_count: int  # consecutive breaching checks required before a stop-loss actually closes
    reversal_confirmation_count: int  # consecutive checks the signal must oppose entry_direction before a reversal-exit closes
    trailing_stop_confirmation_count: int  # consecutive breaching checks required before a trailing-stop pullback actually closes
    # Defaulted (unlike the business-rule numbers above) so the ~20
    # construction sites don't all need an edit. max_hold_trading_days
    # defaults effectively-disabled, same convention as tests' sentinel
    # profit_target_dollars: production sets the real value (1) from
    # settings.max_hold_trading_days via dashboard/context.py.
    max_hold_trading_days: int = 10**9  # force-close a position once it's been open this many trading days, regardless of P&L
    scale_out_fraction: float = 0.5  # fraction of the position to sell when profit_target_dollars is first reached
    # Hard tail stop: close the whole position immediately (no confirmation
    # streak) once it's down this fraction of the premium paid. The normal
    # stop_loss_pct needs stop_loss_confirmation_count consecutive breaching
    # checks (position_check_interval_seconds apart) before it acts — fine for
    # filtering a noisy quote, but on a fast gap a cheap OTM option can run
    # from -25% to worthless inside that window. Defaults effectively-disabled
    # (1.0 = a total loss); production sets the real value from settings.
    catastrophic_stop_pct: float = 1.0

    def __post_init__(self):
        for name in ("stop_loss_pct", "profit_target_dollars", "trailing_stop_pct", "catastrophic_stop_pct"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.catastrophic_stop_pct < self.stop_loss_pct:
            raise ValueError("catastrophic_stop_pct must be >= stop_loss_pct")
        if self.min_trading_days_before_expiry < 0:
            raise ValueError("min_trading_days_before_expiry must be >= 0")
        if self.stop_loss_confirmation_count < 1:
            raise ValueError("stop_loss_confirmation_count must be >= 1")
        if self.reversal_confirmation_count < 1:
            raise ValueError("reversal_confirmation_count must be >= 1")
        if self.trailing_stop_confirmation_count < 1:
            raise ValueError("trailing_stop_confirmation_count must be >= 1")
        if self.max_hold_trading_days < 1:
            raise ValueError("max_hold_trading_days must be >= 1")
        if not 0 < self.scale_out_fraction < 1:
            raise ValueError("scale_out_fraction must be in (0, 1)")


@dataclass(frozen=True)
class PositionState:
    """Tracks one open strategy's lifecycle for exit decisions."""

    symbol: str  # underlying symbol
    qty: int  # remaining open spread units/contracts
    entry_cost_per_unit: float  # net debit paid per unit, in dollars (100x multiplier, same scale as OptionStrategy.net_debit)
    scaled_out: bool = False
    peak_gain_pct: float = 0.0  # highest unrealized gain % observed since entry
    stop_loss_streak: int = 0  # consecutive position-checks where unrealized loss has breached stop_loss_pct
    reversal_streak: int = 0  # consecutive position-checks where the current signal has opposed the entry direction
    trailing_stop_streak: int = 0  # consecutive position-checks where the pullback from peak gain has breached trailing_stop_pct


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
    PROFIT_TARGET = "profit_target"
    SCALE_OUT = "scale_out"
    TRAILING_STOP = "trailing_stop"
    EXPIRY_EXIT = "expiry_exit"
    REVERSAL_EXIT = "reversal_exit"
    MAX_HOLD_EXIT = "max_hold_exit"  # force-close: position held its max allowed trading days


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction
    qty_to_close: int
    reason: str
    stop_loss_streak: int  # streak value the caller should persist on PositionState, win or lose this check
    reversal_streak: int = 0  # streak value the caller should persist on PositionState, win or lose this check
    trailing_stop_streak: int = 0  # streak value the caller should persist on PositionState, win or lose this check
