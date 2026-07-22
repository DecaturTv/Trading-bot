from risk.kelly import TradeStatistics
from risk.statistics import compute_trade_statistics

from .trade_outcome_repository import TradeOutcomeRepository


async def get_live_trade_statistics(
    repository: TradeOutcomeRepository, lookback: int | None = None
) -> TradeStatistics | None:
    """The live counterpart to backtesting.statistics.compute_trade_statistics
    — this is what actually feeds risk.KellySizer once the system has real
    trade history, per this module's "win-rate tracking for Kelly sizing"
    charter in the build order. lookback=None uses the full trade history;
    an int uses only the N most recent closed trades (a rolling window, in
    case older performance shouldn't count as heavily toward current sizing).
    """
    pnls = await repository.recent_pnls(limit=lookback)
    return compute_trade_statistics(pnls)
