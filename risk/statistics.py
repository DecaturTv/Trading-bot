from collections.abc import Sequence

from .kelly import TradeStatistics


def compute_trade_statistics(pnls: Sequence[float]) -> TradeStatistics | None:
    """Turns a raw PnL history into KellySizer inputs — shared by backtesting/
    (simulated trade history) and ml/ (live trade outcome tracking), so both
    feed Kelly sizing identically. Returns None (KellySizer's fallback) if
    there isn't yet both a winning and a losing trade to compute a payoff
    ratio from.
    """
    if not pnls:
        return None
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    if avg_win <= 0 or avg_loss <= 0:
        return None
    return TradeStatistics(win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss, sample_size=len(pnls))
