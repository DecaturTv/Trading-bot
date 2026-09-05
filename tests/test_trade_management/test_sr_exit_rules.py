import pytest

from decision_engine.models import TradeDirection
from trade_management.sr_exit_rules import SRExitAction, SRExitConfig, evaluate_sr_exit


def make_config(**overrides):
    defaults = dict(max_hold_trading_days=3, stop_confirmation_count=2)
    defaults.update(overrides)
    return SRExitConfig(**defaults)


class TestSRExitConfig:
    def test_rejects_non_positive_max_hold(self):
        with pytest.raises(ValueError):
            make_config(max_hold_trading_days=0)

    def test_rejects_non_positive_stop_confirmation_count(self):
        with pytest.raises(ValueError):
            make_config(stop_confirmation_count=0)


class TestEvaluateSrExitLong:
    def test_no_exit_between_stop_and_target(self):
        config = make_config()
        decision = evaluate_sr_exit(
            direction=TradeDirection.BULLISH, stop_price=95.0, target_price=110.0, current_price=101.0,
            trading_days_held=0, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.NONE
        assert decision.stop_streak == 0

    def test_hits_profit_target_immediately_no_confirmation_needed(self):
        config = make_config()
        decision = evaluate_sr_exit(
            direction=TradeDirection.BULLISH, stop_price=95.0, target_price=110.0, current_price=110.5,
            trading_days_held=0, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.PROFIT_TARGET

    def test_stop_breach_waits_for_confirmation(self):
        config = make_config(stop_confirmation_count=2)
        decision = evaluate_sr_exit(
            direction=TradeDirection.BULLISH, stop_price=95.0, target_price=110.0, current_price=94.0,
            trading_days_held=0, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.NONE
        assert decision.stop_streak == 1

    def test_stop_closes_after_confirmation_count_reached(self):
        config = make_config(stop_confirmation_count=2)
        decision = evaluate_sr_exit(
            direction=TradeDirection.BULLISH, stop_price=95.0, target_price=110.0, current_price=94.0,
            trading_days_held=0, stop_streak=1, config=config,
        )
        assert decision.action is SRExitAction.STOP_LOSS
        assert decision.stop_streak == 2

    def test_max_hold_exit_overrides_everything(self):
        config = make_config(max_hold_trading_days=1)
        decision = evaluate_sr_exit(
            direction=TradeDirection.BULLISH, stop_price=95.0, target_price=110.0, current_price=101.0,
            trading_days_held=1, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.MAX_HOLD_EXIT
        assert decision.stop_streak == 0

    def test_target_check_uses_gte(self):
        config = make_config()
        decision = evaluate_sr_exit(
            direction=TradeDirection.BULLISH, stop_price=95.0, target_price=110.0, current_price=110.0,
            trading_days_held=0, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.PROFIT_TARGET


class TestEvaluateSrExitShort:
    def test_no_exit_between_target_and_stop(self):
        config = make_config()
        decision = evaluate_sr_exit(
            direction=TradeDirection.BEARISH, stop_price=110.0, target_price=95.0, current_price=101.0,
            trading_days_held=0, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.NONE

    def test_hits_profit_target_when_price_falls_to_target(self):
        config = make_config()
        decision = evaluate_sr_exit(
            direction=TradeDirection.BEARISH, stop_price=110.0, target_price=95.0, current_price=94.0,
            trading_days_held=0, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.PROFIT_TARGET

    def test_stop_breach_when_price_rises_to_stop(self):
        config = make_config(stop_confirmation_count=1)
        decision = evaluate_sr_exit(
            direction=TradeDirection.BEARISH, stop_price=110.0, target_price=95.0, current_price=111.0,
            trading_days_held=0, stop_streak=0, config=config,
        )
        assert decision.action is SRExitAction.STOP_LOSS


class TestStreakReset:
    def test_stop_streak_resets_to_zero_on_target_or_none(self):
        config = make_config(stop_confirmation_count=3)
        # Was accumulating a stop streak, but this check comes back clean.
        decision = evaluate_sr_exit(
            direction=TradeDirection.BULLISH, stop_price=95.0, target_price=110.0, current_price=101.0,
            trading_days_held=0, stop_streak=2, config=config,
        )
        assert decision.action is SRExitAction.NONE
        assert decision.stop_streak == 0
