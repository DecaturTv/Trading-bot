from risk.kelly import TradeStatistics
from risk.statistics import compute_trade_statistics as _compute_trade_statistics

from .models import SimulatedTrade


def compute_trade_statistics(trades: list[SimulatedTrade]) -> TradeStatistics | None:
    """Feeds risk.KellySizer — this is the whole reason backtesting/ exists in
    the build order: generating win-rate/win-loss data before any live trade
    history exists.
    """
    return _compute_trade_statistics([t.pnl for t in trades])
