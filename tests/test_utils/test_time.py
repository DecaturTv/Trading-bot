from datetime import datetime
from zoneinfo import ZoneInfo

from utils.time import EASTERN, is_equity_market_open, is_forex_market_open

ET = ZoneInfo("America/New_York")


def et(*args) -> datetime:
    return datetime(*args, tzinfo=ET)


def test_equity_market_open_during_session():
    assert is_equity_market_open(et(2026, 7, 21, 10, 0)) is True  # Tuesday


def test_equity_market_closed_before_open():
    assert is_equity_market_open(et(2026, 7, 21, 9, 0)) is False


def test_equity_market_closed_after_close():
    assert is_equity_market_open(et(2026, 7, 21, 16, 30)) is False


def test_equity_market_closed_on_weekend():
    assert is_equity_market_open(et(2026, 7, 25, 10, 0)) is False  # Saturday


def test_forex_closed_on_saturday():
    assert is_forex_market_open(et(2026, 7, 25, 12, 0)) is False


def test_forex_open_sunday_evening():
    assert is_forex_market_open(et(2026, 7, 26, 18, 0)) is True  # Sunday after 17:00


def test_forex_closed_sunday_afternoon():
    assert is_forex_market_open(et(2026, 7, 26, 12, 0)) is False


def test_forex_closed_friday_evening():
    assert is_forex_market_open(et(2026, 7, 24, 18, 0)) is False


def test_forex_open_friday_morning():
    assert is_forex_market_open(et(2026, 7, 24, 10, 0)) is True


def test_forex_open_midweek():
    assert is_forex_market_open(et(2026, 7, 22, 3, 0)) is True  # Wednesday 3am


def test_eastern_constant_matches():
    assert EASTERN.key == "America/New_York"
