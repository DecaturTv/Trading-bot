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
    trading_days_held: int = 0,
    current_confidence: float | None = None,
    underlying_close: float | None = None,
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

    trading_days_held is how many trading days the position has been open;
    once it reaches config.max_hold_trading_days the position is normally
    force-closed (MAX_HOLD_EXIT) ahead of every other rule. current_confidence
    (the same freshly re-scored signal current_direction comes from) can
    override that one check: if config.conviction_hold_confidence_floor is
    set and current_confidence still clears it with current_direction still
    agreeing with entry_direction, the cap is skipped for this cycle and
    every other rule below evaluates normally instead -- a position stays
    open past the calendar cap only for as long as the model's own read on it
    keeps saying so, not on a blind timer. Callers that don't track
    trading_days_held/current_confidence leave the defaults and get the
    original unconditional-cutoff behavior.

    underlying_close is the underlying's latest completed-bar close. When it's
    given and the position carries an underlying stop (PositionState.
    underlying_stop_price), the stop is judged on it instead of on the
    option's -stop_loss_pct premium loss; see _underlying_stop_breached.
    """
    gain_pct = unrealized_gain_pct(position.entry_cost_per_unit, current_value_per_unit)

    uses_underlying_stop = (
        underlying_close is not None
        and position.underlying_stop_price is not None
        and position.entry_underlying_price is not None
    )

    # Hard tail stop, checked before everything. It backstops the gap an
    # option can fall through before the normal stop acts. It used to fire on
    # a single quote, which on thin books sold positions for pennies within
    # minutes of entry (HPQ 0.95 -> 0.02 in 2 min, on a quote the market
    # never traded at again; see project memory on the wick-out analysis).
    # Now it needs stop_loss_confirmation_count consecutive breaching checks,
    # and with an underlying stop the underlying must also be trading against
    # the entry: a collapsed option quote with a flat stock is a bad quote,
    # not a loss.
    if gain_pct <= -config.catastrophic_stop_pct and (
        not uses_underlying_stop or _underlying_against_entry(position, underlying_close)
    ):
        catastrophic_streak = position.catastrophic_streak + 1
    else:
        catastrophic_streak = 0
    if catastrophic_streak >= config.stop_loss_confirmation_count:
        return ExitDecision(
            action=ExitAction.STOP_LOSS,
            qty_to_close=position.qty,
            reason=(
                f"unrealized loss {gain_pct:.1%} breached catastrophic stop "
                f"-{config.catastrophic_stop_pct:.1%} for {catastrophic_streak} consecutive checks"
            ),
            stop_loss_streak=position.stop_loss_streak,
            reversal_streak=0,
            trailing_stop_streak=0,
            catastrophic_streak=catastrophic_streak,
        )

    # Hard time cap, checked before anything else: a position that's been open
    # its maximum allowed trading days is force-closed at the current mark
    # regardless of P&L -- unless conviction_hold_confidence_floor is set and
    # the model still likes this trade at least as much as it did going in,
    # in which case the cap is skipped this cycle and every rule below still
    # applies (stop-loss/reversal/expiry can still close it same as always;
    # conviction only earns a reprieve from the calendar, nothing else).
    # Callers that don't track holding time (default trading_days_held=0)
    # never trip this either way.
    if trading_days_held >= config.max_hold_trading_days:
        holding_on_conviction = (
            config.conviction_hold_confidence_floor is not None
            and current_confidence is not None
            and current_confidence >= config.conviction_hold_confidence_floor
            and current_direction is not None
            and entry_direction is not None
            and current_direction is entry_direction
        )
        if not holding_on_conviction:
            return ExitDecision(
                action=ExitAction.MAX_HOLD_EXIT,
                qty_to_close=position.qty,
                reason=f"held {trading_days_held} trading day(s) >= max {config.max_hold_trading_days}",
                stop_loss_streak=0,
                reversal_streak=0,
                trailing_stop_streak=0,
            )

    if trading_days_to_expiry <= config.min_trading_days_before_expiry:
        return ExitDecision(
            action=ExitAction.EXPIRY_EXIT,
            qty_to_close=position.qty,
            reason=f"{trading_days_to_expiry} trading days to expiration <= minimum {config.min_trading_days_before_expiry}",
            stop_loss_streak=0,
            reversal_streak=0,
            trailing_stop_streak=0,
            catastrophic_streak=catastrophic_streak,
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
    if uses_underlying_stop:
        # A completed-bar close is its own confirmation (the position check
        # re-reads the same bar every cycle), so no streak here.
        stop_loss_streak = 0
        if _underlying_stop_breached(position, underlying_close):
            return ExitDecision(
                action=ExitAction.STOP_LOSS,
                qty_to_close=position.qty,
                reason=(
                    f"underlying closed at {underlying_close:.2f}, through stop {position.underlying_stop_price:.2f} "
                    f"(entry {position.entry_underlying_price:.2f}); option at {gain_pct:.1%}"
                ),
                stop_loss_streak=0,
                reversal_streak=reversal_streak,
                trailing_stop_streak=trailing_stop_streak,
                catastrophic_streak=catastrophic_streak,
            )
    elif gain_pct <= -config.stop_loss_pct:
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
                catastrophic_streak=catastrophic_streak,
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
            catastrophic_streak=catastrophic_streak,
        )

    if not uses_underlying_stop and gain_pct <= -config.stop_loss_pct:
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
            catastrophic_streak=catastrophic_streak,
        )

    dollar_gain = position.qty * (current_value_per_unit - position.entry_cost_per_unit)
    if not position.scaled_out and dollar_gain >= config.profit_target_dollars:
        scale_qty = int(position.qty * config.scale_out_fraction)
        if scale_qty >= 1:
            # Bank part of the gain now; the caller flips scaled_out=True on
            # the remainder, which then rides the trailing stop below.
            return ExitDecision(
                action=ExitAction.SCALE_OUT,
                qty_to_close=scale_qty,
                reason=(
                    f"unrealized gain ${dollar_gain:.2f} reached profit target ${config.profit_target_dollars:.2f}; "
                    f"scaling out {scale_qty}/{position.qty}"
                ),
                stop_loss_streak=stop_loss_streak,
                reversal_streak=reversal_streak,
                trailing_stop_streak=trailing_stop_streak,
                catastrophic_streak=catastrophic_streak,
            )
        # Position too small to split (e.g. a single contract) — take it all.
        return ExitDecision(
            action=ExitAction.PROFIT_TARGET,
            qty_to_close=position.qty,
            reason=f"unrealized gain ${dollar_gain:.2f} reached profit target ${config.profit_target_dollars:.2f}",
            stop_loss_streak=stop_loss_streak,
            reversal_streak=reversal_streak,
            trailing_stop_streak=trailing_stop_streak,
            catastrophic_streak=catastrophic_streak,
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
                    catastrophic_streak=catastrophic_streak,
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


def _is_long_underlying(position: PositionState) -> bool:
    """Calls (long the underlying) put the stop below the entry price, puts above."""
    return position.underlying_stop_price < position.entry_underlying_price


def _underlying_stop_breached(position: PositionState, underlying_close: float) -> bool:
    if _is_long_underlying(position):
        return underlying_close <= position.underlying_stop_price
    return underlying_close >= position.underlying_stop_price


def _underlying_against_entry(position: PositionState, underlying_close: float) -> bool:
    if _is_long_underlying(position):
        return underlying_close < position.entry_underlying_price
    return underlying_close > position.entry_underlying_price
