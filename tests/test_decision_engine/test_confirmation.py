from decision_engine.confirmation import is_confirmed, update_streak
from decision_engine.models import TradeDirection


def test_first_bullish_signal_starts_streak_at_one():
    assert update_streak(TradeDirection.BULLISH, previous_direction=None, previous_streak=0) == 1


def test_repeated_same_direction_increments_streak():
    assert update_streak(TradeDirection.BULLISH, previous_direction=TradeDirection.BULLISH, previous_streak=2) == 3


def test_direction_flip_restarts_streak_at_one():
    assert update_streak(TradeDirection.BEARISH, previous_direction=TradeDirection.BULLISH, previous_streak=4) == 1


def test_neutral_resets_streak_to_zero():
    assert update_streak(TradeDirection.NEUTRAL, previous_direction=TradeDirection.BULLISH, previous_streak=3) == 0


def test_is_confirmed_true_once_streak_reaches_required_count():
    assert is_confirmed(streak=3, required_count=3) is True
    assert is_confirmed(streak=4, required_count=3) is True


def test_is_confirmed_false_below_required_count():
    assert is_confirmed(streak=2, required_count=3) is False
