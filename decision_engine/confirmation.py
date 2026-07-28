from .models import TradeDirection


def update_streak(current_direction: TradeDirection, previous_direction: TradeDirection | None, previous_streak: int) -> int:
    """Consecutive-scan persistence counter for a directional signal.

    Resets to 0 on NEUTRAL (no signal to persist), restarts at 1 when the
    direction flips to the other side, and increments while the same
    non-neutral direction repeats. Mirrors the stop_loss_streak pattern in
    trade_management/exit_rules.py, generalized to any BULLISH/BEARISH call
    (new entries, or a reversal against an open position) that shouldn't act
    on a single noisy tick.
    """
    if current_direction is TradeDirection.NEUTRAL:
        return 0
    if current_direction != previous_direction:
        return 1
    return previous_streak + 1


def is_confirmed(streak: int, required_count: int) -> bool:
    return streak >= required_count
