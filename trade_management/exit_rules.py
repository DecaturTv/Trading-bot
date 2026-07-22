from .models import ExitAction, ExitDecision, PositionState, TradeManagementConfig
from .pnl import unrealized_gain_pct


def evaluate_exit(
    position: PositionState,
    current_value_per_unit: float,
    trading_days_to_expiry: int,
    config: TradeManagementConfig,
) -> ExitDecision:
    """Pure decision function — evaluates one snapshot in time. Peak-gain
    tracking for the trailing stop is caller-managed state (see
    PositionStateRepository): this function doesn't mutate position, so the
    caller must persist an updated peak_gain_pct between calls.
    """
    gain_pct = unrealized_gain_pct(position.entry_cost_per_unit, current_value_per_unit)

    if trading_days_to_expiry <= config.min_trading_days_before_expiry:
        return ExitDecision(
            action=ExitAction.EXPIRY_EXIT,
            qty_to_close=position.qty,
            reason=f"{trading_days_to_expiry} trading days to expiration <= minimum {config.min_trading_days_before_expiry}",
        )

    if gain_pct <= -config.stop_loss_pct:
        return ExitDecision(
            action=ExitAction.STOP_LOSS,
            qty_to_close=position.qty,
            reason=f"unrealized loss {gain_pct:.1%} breached stop-loss -{config.stop_loss_pct:.1%}",
        )

    if not position.scaled_out and gain_pct >= config.profit_target_pct:
        qty_to_close = max(1, round(position.qty * config.scale_out_fraction))
        return ExitDecision(
            action=ExitAction.SCALE_OUT,
            qty_to_close=qty_to_close,
            reason=f"unrealized gain {gain_pct:.1%} reached profit target {config.profit_target_pct:.1%}",
        )

    if position.scaled_out:
        peak = max(position.peak_gain_pct, gain_pct)
        pullback = peak - gain_pct
        if pullback >= config.trailing_stop_pct:
            return ExitDecision(
                action=ExitAction.TRAILING_STOP,
                qty_to_close=position.qty,
                reason=f"pulled back {pullback:.1%} from peak gain {peak:.1%}, trailing stop {config.trailing_stop_pct:.1%}",
            )

    return ExitDecision(action=ExitAction.NONE, qty_to_close=0, reason="no exit condition met")
