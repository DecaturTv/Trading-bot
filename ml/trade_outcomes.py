from risk.kelly import TradeStatistics
from risk.statistics import compute_trade_statistics

from .trade_outcome_repository import TradeOutcomeRepository


async def get_live_trade_statistics(
    repository: TradeOutcomeRepository, lookback: int | None = None, asset_class: str | None = None
) -> TradeStatistics | None:
    """The live counterpart to backtesting.statistics.compute_trade_statistics
    — this is what actually feeds risk.KellySizer once the system has real
    trade history, per this module's "win-rate tracking for Kelly sizing"
    charter in the build order. lookback=None uses the full trade history;
    an int uses only the N most recent closed trades (a rolling window, in
    case older performance shouldn't count as heavily toward current sizing).
    asset_class scopes to one account's history (e.g. "equities") so Kelly
    sizing isn't calibrated off a different account's win rate.
    """
    pnls = await repository.recent_pnls(limit=lookback, asset_class=asset_class)
    return compute_trade_statistics(pnls)
