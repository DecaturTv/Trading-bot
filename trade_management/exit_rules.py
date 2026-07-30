from decision_engine.models import TradeDirection

from .models import ExitAction, ExitDecision, PositionState, TradeManagementConfig
from .pnl import unrealized_gain_pct


def evaluate_exit(
    position: PositionState,
    current_value_per_unit: float,
    trading_days_to_expiry: int,
    config: TradeManagementConfig,
    current_direction: TradeDirection | None = None,
    entry_direction: TradeDirection | None = None,
) -> ExitDecision:
    """Pure decision function — evaluates one snapshot in time. Peak-gain
    tracking for the trailing stop, and the stop-loss/reversal/trailing-stop
    confirmation streaks below, are all caller-managed state (see
    PositionStateRepository): this function doesn't mutate position, so the
    caller must persist the updated
    peak_gain_pct/stop_loss_streak/reversal_streak/trailing_stop_streak it
    returns between calls.

    current_direction/entry_direction are optional: pass both to enable the
    reversal-exit check (current signal direction vs. the direction the
    position was opened on); omitting either treats this cycle as
    non-opposing (reversal_streak resets to 0), e.g. when the caller couldn't
    compute a fresh signal this cycle.
    """
    gain_pct = unrealized_gain_pct(position.entry_cost_per_unit, current_value_per_unit)

    if trading_days_to_expiry <= config.min_trading_days_before_expiry:
        return ExitDecision(
            action=ExitAction.EXPIRY_EXIT,
            qty_to_close=position.qty,
            reason=f"{trading_days_to_expiry} trading days to expiration <= minimum {config.min_trading_days_before_expiry}",
            stop_loss_streak=0,
            reversal_streak=0,
            trailing_stop_streak=0,
        )

    # Require the reversal to hold across N consecutive checks before acting
    # on it, same rationale as the stop-loss confirmation streak below — a
    # single noisy scan flipping direction shouldn't be enough to close a
    # position that's otherwise fine. See config/settings.py
    # signal_confirmation_count and project memory on the reversal fix.
    opposed = (
        current_direction is not None
        and entry_direction is not None
        and current_direction is not TradeDirection.NEUTRAL
        and current_direction is not entry_direction
    )
    reversal_streak = position.reversal_streak + 1 if opposed else 0

    trailing_stop_streak = position.trailing_stop_streak
    stop_loss_streak = position.stop_loss_streak
    if gain_pct <= -config.stop_loss_pct:
        stop_loss_streak = position.stop_loss_streak + 1
        # Require the breach to hold across N consecutive checks before acting
        # on it — a single noisy quote (wide bid/ask on a thin option) can
        # otherwise trip the stop even though nothing about the underlying
        # actually moved against the position. See project memory on the ACI
        # trade that motivated this.
        if stop_loss_streak >= config.stop_loss_confirmation_count:
            return ExitDecision(
                action=ExitAction.STOP_LOSS,
                qty_to_close=position.qty,
                reason=(
                    f"unrealized loss {gain_pct:.1%} breached stop-loss -{config.stop_loss_pct:.1%} "
                    f"for {stop_loss_streak}/{config.stop_loss_confirmation_count} consecutive checks"
                ),
                stop_loss_streak=stop_loss_streak,
                reversal_streak=reversal_streak,
                trailing_stop_streak=trailing_stop_streak,
            )
    else:
        stop_loss_streak = 0

    if reversal_streak >= config.reversal_confirmation_count:
        return ExitDecision(
            action=ExitAction.REVERSAL_EXIT,
            qty_to_close=position.qty,
            reason=(
                f"signal reversed to {current_direction.value} against entry direction {entry_direction.value} "
                f"for {reversal_streak}/{config.reversal_confirmation_count} consecutive checks"
            ),
            stop_loss_streak=stop_loss_streak,
            reversal_streak=reversal_streak,
            trailing_stop_streak=trailing_stop_streak,
        )

    if gain_pct <= -config.stop_loss_pct:
        return ExitDecision(
            action=ExitAction.NONE,
            qty_to_close=0,
            reason=(
                f"unrealized loss {gain_pct:.1%} breached stop-loss -{config.stop_loss_pct:.1%}, "
                f"awaiting confirmation ({stop_loss_streak}/{config.stop_loss_confirmation_count})"
            ),
            stop_loss_streak=stop_loss_streak,
            reversal_streak=reversal_streak,
            trailing_stop_streak=trailing_stop_streak,
        )

    if not position.scaled_out and gain_pct >= config.profit_target_pct:
        qty_to_close = max(1, round(position.qty * config.scale_out_fraction))
        return ExitDecision(
            action=ExitAction.SCALE_OUT,
            qty_to_close=qty_to_close,
            reason=f"unrealized gain {gain_pct:.1%} reached profit target {config.profit_target_pct:.1%}",
            stop_loss_streak=stop_loss_streak,
            reversal_streak=reversal_streak,
            trailing_stop_streak=trailing_stop_streak,
        )

    if position.scaled_out:
        peak = max(position.peak_gain_pct, gain_pct)
        pullback = peak - gain_pct
        if pullback >= config.trailing_stop_pct:
            trailing_stop_streak = position.trailing_stop_streak + 1
            # Require the pullback to hold for N consecutive checks before
            # closing -- same rationale as the stop-loss confirmation streak
            # above: current_value_per_unit is a mid-price snapshot, and a
            # single noisy quote (wide bid/ask on a thin option) can trip a
            # pullback that hasn't actually happened. See project memory on
            # the ACI trade that motivated the equivalent stop-loss guard.
            if trailing_stop_streak >= config.trailing_stop_confirmation_count:
                return ExitDecision(
                    action=ExitAction.TRAILING_STOP,
                    qty_to_close=position.qty,
                    reason=(
                        f"pulled back {pullback:.1%} from peak gain {peak:.1%}, trailing stop {config.trailing_stop_pct:.1%} "
                        f"for {trailing_stop_streak}/{config.trailing_stop_confirmation_count} consecutive checks"
                    ),
                    stop_loss_streak=stop_loss_streak,
                    reversal_streak=reversal_streak,
                    trailing_stop_streak=trailing_stop_streak,
                )
        else:
            trailing_stop_streak = 0

    return ExitDecision(
        action=ExitAction.NONE,
        qty_to_close=0,
        reason="no exit condition met",
        stop_loss_streak=stop_loss_streak,
        reversal_streak=reversal_streak,
        trailing_stop_streak=trailing_stop_streak,
    )
