from datetime import date

import pytest

from risk.streak import current_positive_day_streak, streak_adjusted_fraction


def test_current_positive_day_streak_counts_consecutive_positive_days():
    daily_pnls = [(date(2026, 8, 10), 50.0), (date(2026, 8, 9), 20.0), (date(2026, 8, 8), 10.0)]
    assert current_positive_day_streak(daily_pnls) == 3


def test_current_positive_day_streak_stops_at_first_non_positive_day():
    daily_pnls = [(date(2026, 8, 10), 50.0), (date(2026, 8, 9), -5.0), (date(2026, 8, 8), 10.0)]
    assert current_positive_day_streak(daily_pnls) == 1


def test_current_positive_day_streak_zero_day_breaks_streak():
    daily_pnls = [(date(2026, 8, 10), 0.0), (date(2026, 8, 9), 10.0)]
    assert current_positive_day_streak(daily_pnls) == 0


def test_current_positive_day_streak_empty_history_is_zero():
    assert current_positive_day_streak([]) == 0


def test_streak_adjusted_fraction_no_streak_is_unchanged():
    assert streak_adjusted_fraction(0.5, streak=0) == pytest.approx(0.5)


def test_streak_adjusted_fraction_decays_with_streak():
    # multiplier = 1 - 3*0.1 = 0.7
    assert streak_adjusted_fraction(0.5, streak=3) == pytest.approx(0.35)


def test_streak_adjusted_fraction_floors_at_min_multiplier():
    # multiplier would go to 1 - 20*0.1 = -1.0, floored at min_multiplier=0.4
    assert streak_adjusted_fraction(0.5, streak=20) == pytest.approx(0.2)


def test_streak_adjusted_fraction_respects_custom_params():
    result = streak_adjusted_fraction(1.0, streak=2, decay_per_streak_day=0.2, min_multiplier=0.5)
    assert result == pytest.approx(0.6)  # 1 - 2*0.2 = 0.6, above the floor
