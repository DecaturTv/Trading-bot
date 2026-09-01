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


def test_catastrophic_stop_closes_immediately_without_confirmation():
    # down 60% (200 vs 500 entry); catastrophic at -50%, confirmation would
    # otherwise need 3 checks
    config = make_config(stop_loss_pct=0.25, catastrophic_stop_pct=0.50, stop_loss_confirmation_count=3)
    position = make_position(entry_cost_per_unit=500.0, stop_loss_streak=0)

    decision = evaluate_exit(position, current_value_per_unit=200.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.STOP_LOSS
    assert decision.qty_to_close == position.qty
    assert "catastrophic" in decision.reason


def test_catastrophic_stop_fires_even_past_max_hold():
    config = make_config(stop_loss_pct=0.25, catastrophic_stop_pct=0.50, max_hold_trading_days=1)
    position = make_position(entry_cost_per_unit=500.0)

    decision = evaluate_exit(
        position, current_value_per_unit=100.0, trading_days_to_expiry=10, config=config, trading_days_held=5
    )

    assert decision.action is ExitAction.STOP_LOSS
    assert "catastrophic" in decision.reason


def test_normal_stop_still_waits_when_loss_below_catastrophic_threshold():
    # down 30%: past the -25% stop but not the -50% catastrophic one, so the
    # confirmation streak still applies
    config = make_config(stop_loss_pct=0.25, catastrophic_stop_pct=0.50, stop_loss_confirmation_count=2)
    position = make_position(entry_cost_per_unit=500.0, stop_loss_streak=0)

    decision = evaluate_exit(position, current_value_per_unit=350.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE
    assert decision.stop_loss_streak == 1


def test_config_rejects_catastrophic_stop_below_stop_loss():
    with pytest.raises(ValueError, match="catastrophic_stop_pct"):
        make_config(stop_loss_pct=0.50, catastrophic_stop_pct=0.25)


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
            stop_loss_pct=0.5, profit_target_dollars=50.0, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=1, reversal_confirmation_count=0,
            trailing_stop_confirmation_count=1,
        )


def test_profit_target_scales_out_a_fraction_at_dollar_gain():
    config = make_config(profit_target_dollars=50.0, scale_out_fraction=0.5)
    position = make_position(qty=4, entry_cost_per_unit=500.0, scaled_out=False)

    # dollar gain = 4 * (515 - 500) = 60 >= 50
    decision = evaluate_exit(position, current_value_per_unit=515.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.SCALE_OUT
    assert decision.qty_to_close == 2  # int(4 * 0.5)


def test_profit_target_full_closes_when_position_too_small_to_split():
    config = make_config(profit_target_dollars=50.0, scale_out_fraction=0.5)
    position = make_position(qty=1, entry_cost_per_unit=500.0, scaled_out=False)

    # dollar gain = 1 * (560 - 500) = 60 >= 50; int(1 * 0.5) == 0 -> take it all
    decision = evaluate_exit(position, current_value_per_unit=560.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.PROFIT_TARGET
    assert decision.qty_to_close == 1


def test_max_hold_exit_force_closes_ahead_of_every_other_rule():
    config = make_config(max_hold_trading_days=1, stop_loss_pct=0.50, profit_target_dollars=50.0)
    # Deep in profit AND past its holding-time cap: the cap wins.
    position = make_position(qty=4, entry_cost_per_unit=500.0)

    decision = evaluate_exit(
        position, current_value_per_unit=900.0, trading_days_to_expiry=10, config=config, trading_days_held=1
    )

    assert decision.action is ExitAction.MAX_HOLD_EXIT
    assert decision.qty_to_close == position.qty


def test_max_hold_exit_does_not_fire_before_the_cap():
    config = make_config(max_hold_trading_days=2)
    position = make_position(entry_cost_per_unit=500.0)

    decision = evaluate_exit(
        position, current_value_per_unit=505.0, trading_days_to_expiry=10, config=config, trading_days_held=1
    )

    assert decision.action is ExitAction.NONE


def test_profit_target_does_not_trigger_below_dollar_gain():
    config = make_config(profit_target_dollars=50.0)
    position = make_position(qty=4, entry_cost_per_unit=500.0, scaled_out=False)

    # dollar gain = 4 * (510 - 500) = 40 < 50
    decision = evaluate_exit(position, current_value_per_unit=510.0, trading_days_to_expiry=10, config=config)

    assert decision.action is ExitAction.NONE


def test_profit_target_does_not_retrigger_once_already_scaled_out():
    config = make_config(profit_target_dollars=50.0)
    position = make_position(entry_cost_per_unit=500.0, scaled_out=True, peak_gain_pct=1.5)

    decision = evaluate_exit(position, current_value_per_unit=1400.0, trading_days_to_expiry=10, config=config)

    assert decision.action is not ExitAction.PROFIT_TARGET


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
            stop_loss_pct=0.5, profit_target_dollars=50.0, trailing_stop_pct=0.2,
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
            stop_loss_pct=0.0, profit_target_dollars=50.0, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=1, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )


def test_config_rejects_non_positive_profit_target_dollars():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_dollars=0.0, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=1, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )


def test_config_rejects_negative_min_dte():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_dollars=50.0, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=-1, stop_loss_confirmation_count=1, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )


def test_config_rejects_non_positive_stop_loss_confirmation_count():
    with pytest.raises(ValueError):
        TradeManagementConfig(
            stop_loss_pct=0.5, profit_target_dollars=50.0, trailing_stop_pct=0.2,
            min_trading_days_before_expiry=2, stop_loss_confirmation_count=0, reversal_confirmation_count=1,
            trailing_stop_confirmation_count=1,
        )


def test_config_rejects_max_hold_trading_days_below_one():
    with pytest.raises(ValueError):
        make_config(max_hold_trading_days=0)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
def test_config_rejects_scale_out_fraction_outside_open_unit_interval(bad):
    with pytest.raises(ValueError):
        make_config(scale_out_fraction=bad)
