from risk.kelly import TradeStatistics

from .models import SimulatedTrade


def compute_trade_statistics(trades: list[SimulatedTrade]) -> TradeStatistics | None:
    """Feeds risk.KellySizer — this is the whole reason backtesting/ exists in
    the build order: generating win-rate/win-loss data before any live trade
    history exists. Returns None (KellySizer's fallback) if there aren't yet
    both a winning and a losing trade to compute a payoff ratio from.
    """
    if not trades:
        return None
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [-t.pnl for t in trades if t.pnl < 0]
    win_rate = len(wins) / len(trades)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    if avg_win <= 0 or avg_loss <= 0:
        return None
    return TradeStatistics(win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss, sample_size=len(trades))
