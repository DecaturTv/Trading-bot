import pytest
from tm_factories import make_config

from decision_engine.models import TradeDirection
from trade_management.exit_rules import evaluate_exit
from trade_management.models import ExitAction, PositionState, TradeManagementConfig


def make_position(**overrides):
    defaults = dict(
        symbol="AAPL", qty=4, entry_cost_per_unit=500.0, scaled_out=False, peak_gain_pct=0.0,
        stop_loss_streak=0, reversal_streak=0, trailing_stop_streak=0,
    )
    defaults.update(overrides)
    return PositionState(**defaults)


def test_no_exit_when_nothing_triggers():
    config = make_config()
    position = make_position()

    decision = evaluate_exit(position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.qty_to_close == 0


def test_stop_loss_triggers_at_configured_threshold():
    config = make_config(stop_loss_pct=0.50)
    position = make_position(entry_cost_per_unit=500.0)

    decision = evaluate_exit(position, current_value_per_unit=240.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.STOP_LOSS
    assert decision.qty_to_close == position.qty


def test_stop_loss_waits_for_confirmation_before_closing():
    config = make_config(stop_loss_pct=0.50, stop_loss_confirmation_count=3)
    position = make_position(entry_cost_per_unit=500.0, stop_loss_streak=0)

    decision = evaluate_exit(position, current_value_per_unit=240.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.stop_loss_streak == 1


def test_stop_loss_closes_once_confirmation_count_reached():
    config = make_config(stop_loss_pct=0.50, stop_loss_confirmation_count=3)
    position = make_position(entry_cost_per_unit=500.0, stop_loss_streak=2)

    decision = evaluate_exit(position, current_value_per_unit=240.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.STOP_LOSS
    assert decision.qty_to_close == position.qty
    assert decision.stop_loss_streak == 3


def test_stop_loss_streak_resets_once_price_recovers():
    config = make_config(stop_loss_pct=0.50, stop_loss_confirmation_count=3)
    position = make_position(entry_cost_per_unit=500.0, stop_loss_streak=2)

    decision = evaluate_exit(position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.stop_loss_streak == 0


def test_no_reversal_exit_when_current_direction_matches_entry_direction():
    config = make_config(reversal_confirmation_count=1)
    position = make_position()

    decision = evaluate_exit(
        position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config,
        current_direction=TradeDirection.BULLISH, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.NONE
    assert decision.reversal_streak == 0


def test_reversal_exit_triggers_when_confirmation_count_is_one():
    config = make_config(reversal_confirmation_count=1)
    position = make_position()

    decision = evaluate_exit(
        position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config,
        current_direction=TradeDirection.BEARISH, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.REVERSAL_EXIT
    assert decision.qty_to_close == position.qty
    assert decision.reversal_streak == 1


def test_reversal_exit_waits_for_confirmation_before_closing():
    config = make_config(reversal_confirmation_count=3)
    position = make_position(reversal_streak=0)

    decision = evaluate_exit(
        position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config,
        current_direction=TradeDirection.BEARISH, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.NONE
    assert decision.reversal_streak == 1


def test_reversal_exit_closes_once_confirmation_count_reached():
    config = make_config(reversal_confirmation_count=3)
    position = make_position(reversal_streak=2)

    decision = evaluate_exit(
        position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config,
        current_direction=TradeDirection.BEARISH, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.REVERSAL_EXIT
    assert decision.qty_to_close == position.qty
    assert decision.reversal_streak == 3


def test_reversal_streak_resets_once_signal_agrees_with_entry_again():
    config = make_config(reversal_confirmation_count=3)
    position = make_position(reversal_streak=2)

    decision = evaluate_exit(
        position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config,
        current_direction=TradeDirection.BULLISH, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.NONE
    assert decision.reversal_streak == 0


def test_neutral_current_direction_does_not_count_as_opposed():
    config = make_config(reversal_confirmation_count=1)
    position = make_position(reversal_streak=0)

    decision = evaluate_exit(
        position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config,
        current_direction=TradeDirection.NEUTRAL, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.NONE
    assert decision.reversal_streak == 0


def test_reversal_check_skipped_when_direction_not_provided():
    config = make_config(reversal_confirmation_count=1)
    position = make_position(reversal_streak=0)

    decision = evaluate_exit(position, current_value_per_unit=520.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.reversal_streak == 0


def test_stop_loss_takes_priority_over_reversal_exit_when_both_confirmed():
    config = make_config(stop_loss_pct=0.50, stop_loss_confirmation_count=1, reversal_confirmation_count=1)
    position = make_position(entry_cost_per_unit=500.0)

    decision = evaluate_exit(
        position, current_value_per_unit=240.0, trading_days_to_expiry=10, config=config,
        current_direction=TradeDirection.BEARISH, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.STOP_LOSS


def test_expiry_exit_takes_priority_over_reversal_exit():
    config = make_config(min_trading_days_before_expiry=2, reversal_confirmation_count=1)
    position = make_position(entry_cost_per_unit=500.0)

    decision = evaluate_exit(
        position, current_value_per_unit=520.0, trading_days_to_expiry=1, config=config,
        current_direction=TradeDirection.BEARISH, entry_direction=TradeDirection.BULLISH,
    )

    assert decision.action is ExitAction.EXPIRY_EXIT


def test_config_rejects_non_positive_reversal_confirmation_count():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_pct=1.0, scale_out_fraction=0.5, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=1, reversal_confirmation_count=0,
            trailing_stop_confirmation_count=1,
        )


def test_scale_out_triggers_at_profit_target_and_closes_configured_fraction():
    config = make_config(profit_target_pct=1.00, scale_out_fraction=0.50)
    position = make_position(qty=4, entry_cost_per_unit=500.0, scaled_out=False)

    decision = evaluate_exit(position, current_value_per_unit=1010.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.SCALE_OUT
    assert decision.qty_to_close == 2


def test_scale_out_does_not_retrigger_once_already_scaled_out():
    config = make_config(profit_target_pct=1.00)
    position = make_position(entry_cost_per_unit=500.0, scaled_out=True, peak_gain_pct=1.5)

    decision = evaluate_exit(position, current_value_per_unit=1400.0, trading_days_to_expiry=10, config=config)

    assert decision.action is not ExitAction.SCALE_OUT


def test_trailing_stop_triggers_after_scale_out_on_pullback():
    config = make_config(trailing_stop_pct=0.20)
    position = make_position(entry_cost_per_unit=500.0, scaled_out=True, peak_gain_pct=1.50)

    # current gain = (600-500)/500 = 0.20; pullback from peak 1.50 = 1.30 >= 0.20
    decision = evaluate_exit(position, current_value_per_unit=600.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.TRAILING_STOP
    assert decision.qty_to_close == position.qty
    assert decision.trailing_stop_streak == 1


def test_trailing_stop_does_not_trigger_within_tolerance():
    config = make_config(trailing_stop_pct=0.20)
    position = make_position(entry_cost_per_unit=500.0, scaled_out=True, peak_gain_pct=1.00)

    # current gain = (950-500)/500 = 0.90; pullback = 0.10 < 0.20
    decision = evaluate_exit(position, current_value_per_unit=950.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.trailing_stop_streak == 0


def test_trailing_stop_waits_for_confirmation_before_closing():
    config = make_config(trailing_stop_pct=0.20, trailing_stop_confirmation_count=3)
    position = make_position(entry_cost_per_unit=500.0, scaled_out=True, peak_gain_pct=1.50, trailing_stop_streak=0)

    decision = evaluate_exit(position, current_value_per_unit=600.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.trailing_stop_streak == 1


def test_trailing_stop_closes_once_confirmation_count_reached():
    config = make_config(trailing_stop_pct=0.20, trailing_stop_confirmation_count=3)
    position = make_position(entry_cost_per_unit=500.0, scaled_out=True, peak_gain_pct=1.50, trailing_stop_streak=2)

    decision = evaluate_exit(position, current_value_per_unit=600.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.TRAILING_STOP
    assert decision.qty_to_close == position.qty
    assert decision.trailing_stop_streak == 3


def test_trailing_stop_streak_resets_once_back_within_tolerance():
    config = make_config(trailing_stop_pct=0.20, trailing_stop_confirmation_count=3)
    position = make_position(entry_cost_per_unit=500.0, scaled_out=True, peak_gain_pct=1.00, trailing_stop_streak=2)

    # current gain = (950-500)/500 = 0.90; pullback = 0.10 < 0.20
    decision = evaluate_exit(position, current_value_per_unit=950.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.trailing_stop_streak == 0


def test_config_rejects_non_positive_trailing_stop_confirmation_count():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_pct=1.0, scale_out_fraction=0.5, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=1, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=0,
        )


def test_expiry_exit_takes_priority_over_everything_else():
    config = make_config(min_trading_days_before_expiry=2, stop_loss_pct=0.50)
    # Deep in profit, but expiration is imminent — must still force-close.
    position = make_position(entry_cost_per_unit=500.0)

    decision = evaluate_exit(position, current_value_per_unit=2000.0, trading_days_to_expiry=1, config=config)

    assert decision.action is ExitAction.EXPIRY_EXIT
    assert decision.qty_to_close == position.qty


def test_config_rejects_non_positive_stop_loss():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.0, profit_target_pct=1.0, scale_out_fraction=0.5, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=1, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )


def test_config_rejects_invalid_scale_out_fraction():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_pct=1.0, scale_out_fraction=1.5, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=1, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )


def test_config_rejects_negative_min_dte():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_pct=1.0, scale_out_fraction=0.5, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=-1, stop_loss_confirmation_count=1, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )


def test_config_rejects_non_positive_stop_loss_confirmation_count():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_pct=1.0, scale_out_fraction=0.5, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=0, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )
