from datetime import date


def current_positive_day_streak(daily_pnls: list[tuple[date, float]]) -> int:
    """daily_pnls must be ordered most-recent-day first (see
    ml.trade_outcome_repository.TradeOutcomeRepository.daily_pnls) -- a day
    with no closed trades has no row at all, so it's skipped rather than
    counted as a break. Counts consecutive days from the most recent with
    positive net P&L; stops at the first day that isn't."""
    streak = 0
    for _, pnl in daily_pnls:
        if pnl <= 0:
            break
        streak += 1
    return streak


def streak_adjusted_fraction(
    base_fraction: float, streak: int, decay_per_streak_day: float = 0.1, min_multiplier: float = 0.4
) -> float:
    """Scales a position-sizing fraction (Kelly position_fraction, forex
    risk_pct_per_trade) down as the current positive-day streak grows, so a
    single bad trade is less likely to snap a run that's been building --
    there's more to protect the longer the streak runs. Floored at
    min_multiplier so sizing never goes to zero regardless of streak length.
    """
    multiplier = max(min_multiplier, 1 - streak * decay_per_streak_day)
    return base_fraction * multiplier
