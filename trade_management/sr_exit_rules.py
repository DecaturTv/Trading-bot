from dataclasses import dataclass
from enum import Enum

from decision_engine.models import TradeDirection


class SRExitAction(str, Enum):
    NONE = "none"
    STOP_LOSS = "stop_loss"
    PROFIT_TARGET = "profit_target"
    MAX_HOLD_EXIT = "max_hold_exit"


@dataclass(frozen=True)
class SRExitConfig:
    """No defaults on purpose, same "confirmed business decision" convention
    as TradeManagementConfig. max_hold_trading_days=3 matches MAX_HOLD_BARS
    (234 5Min bars) in _scratch_sr_backtest.py -- the backtested edge depends
    on letting a position ride to its level-derived stop/target rather than
    cutting it off early. stop_confirmation_count is a live-only addition:
    the backtest checks stop/target against bar highs/lows, but the live
    position-management cycle polls a single mid-price snapshot instead, so
    it gets the same noisy-quote guard every other stop in this codebase
    requires (see project memory on the ACI trade / stop_loss_confirmation_count).
    Target hits don't need confirmation -- there's no downside to taking a
    real profit a check early."""

    max_hold_trading_days: int
    stop_confirmation_count: int

    def __post_init__(self):
        if self.max_hold_trading_days < 1:
            raise ValueError("max_hold_trading_days must be >= 1")
        if self.stop_confirmation_count < 1:
            raise ValueError("stop_confirmation_count must be >= 1")


# The exact values behind the backtest results (see decision_engine.support_resistance).
BACKTESTED_SR_EXIT_CONFIG = SRExitConfig(max_hold_trading_days=3, stop_confirmation_count=2)


@dataclass(frozen=True)
class SRExitDecision:
    action: SRExitAction
    reason: str
    stop_streak: int  # streak value the caller should persist, win or lose this check


def evaluate_sr_exit(
    direction: TradeDirection,
    stop_price: float,
    target_price: float,
    current_price: float,
    trading_days_held: int,
    stop_streak: int,
    config: SRExitConfig,
) -> SRExitDecision:
    """Pure decision function, same shape as trade_management.exit_rules.evaluate_exit
    but against absolute underlying-price levels instead of %/$ P&L. stop_price
    and target_price are set once at entry from the level structure that
    produced the signal (decision_engine.support_resistance.sr_signal) and
    passed in unchanged here -- this function doesn't recompute levels.

    No scale-out/trailing-stop: the backtested edge comes from letting the
    full position ride to the level-derived target or stop, not banking part
    of it early (see project memory on the S/R backtest / theoretical
    comparison against the momentum model's flat-dollar target).
    """
    if trading_days_held >= config.max_hold_trading_days:
        return SRExitDecision(
            action=SRExitAction.MAX_HOLD_EXIT,
            reason=f"held {trading_days_held} trading day(s) >= max {config.max_hold_trading_days}",
            stop_streak=0,
        )

    if direction is TradeDirection.BULLISH:
        hit_target = current_price >= target_price
        hit_stop = current_price <= stop_price
    else:
        hit_target = current_price <= target_price
        hit_stop = current_price >= stop_price

    if hit_target:
        return SRExitDecision(
            action=SRExitAction.PROFIT_TARGET,
            reason=f"price {current_price:.2f} reached target {target_price:.2f}",
            stop_streak=0,
        )

    if hit_stop:
        streak = stop_streak + 1
        if streak >= config.stop_confirmation_count:
            return SRExitDecision(
                action=SRExitAction.STOP_LOSS,
                reason=(
                    f"price {current_price:.2f} breached stop {stop_price:.2f} "
                    f"for {streak}/{config.stop_confirmation_count} consecutive checks"
                ),
                stop_streak=streak,
            )
        return SRExitDecision(
            action=SRExitAction.NONE,
            reason=f"price {current_price:.2f} breached stop {stop_price:.2f}, awaiting confirmation ({streak}/{config.stop_confirmation_count})",
            stop_streak=streak,
        )

    return SRExitDecision(action=SRExitAction.NONE, reason="no exit condition met", stop_streak=0)
