from datetime import date

from trade_management.expiry import trading_days_until


def test_friday_to_monday_is_one_trading_day():
    assert trading_days_until(date(2026, 7, 27), date(2026, 7, 24)) == 1  # Fri -> Mon


def test_same_day_is_zero():
    assert trading_days_until(date(2026, 7, 24), date(2026, 7, 24)) == 0


def test_past_expiration_is_zero():
    assert trading_days_until(date(2026, 7, 20), date(2026, 7, 24)) == 0


def test_counts_weekdays_only_across_a_full_week():
    # Mon(20) -> next Mon(27): 5 trading days (Tue-Fri + next Mon), weekend skipped
    assert trading_days_until(date(2026, 7, 27), date(2026, 7, 20)) == 5


def test_next_day_weekday_is_one():
    assert trading_days_until(date(2026, 7, 22), date(2026, 7, 21)) == 1  # Tue -> Wed
