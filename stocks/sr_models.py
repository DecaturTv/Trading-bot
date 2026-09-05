from dataclasses import dataclass
from datetime import date

from decision_engine.models import TradeDirection


@dataclass(frozen=True)
class SRStockPositionState:
    """Mirrors trade_management.models.PositionState's role but against
    absolute price levels instead of %/$ P&L -- stop_price/target_price are
    set once at entry from the level structure that produced the signal (see
    decision_engine.support_resistance.sr_signal) and checked as-is by
    trade_management.sr_exit_rules.evaluate_sr_exit. No scaled_out/
    peak_gain_pct/trailing fields: the S/R strategy takes the full position
    off at whichever of stop/target/max-hold hits first, it doesn't scale out."""

    symbol: str
    qty: int
    entry_price: float
    stop_price: float
    target_price: float
    stop_streak: int = 0


@dataclass(frozen=True)
class OpenSRStockPositionRecord:
    """A tracked S/R direct-equity position -- long-only, same constraint as
    OpenStockPositionRecord (a bearish S/R signal is the sr_options_loop's
    long_put territory, not shorting shares)."""

    symbol: str
    direction: TradeDirection
    entry_date: date
    state: SRStockPositionState
