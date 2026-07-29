from datetime import datetime, timedelta, timezone

from broker.models import Bar


def make_bars(closes, volumes=None, spread=1.0, start=None):
    """Weekday-only daily bars (Mon-Fri), matching real trading calendars
    closely enough for trading_days_until to behave sensibly in tests."""
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)  # a Monday
    volumes = volumes or [1000.0] * len(closes)
    bars = []
    current = start
    for i, close in enumerate(closes):
        while current.weekday() >= 5:
            current += timedelta(days=1)
        bars.append(
            Bar(
                symbol="TEST",
                timestamp=current,
                open=close,
                high=close + spread,
                low=close - spread,
                close=close,
                volume=volumes[i],
            )
        )
        current += timedelta(days=1)
    return bars


def make_hourly_bars(closes, pair="EUR_USD", spread=0.001, start=None):
    """Hourly bars for forex backtest tests -- no weekday-skip logic (forex
    trades 24/5, and none of the forex engine's logic cares about calendar
    days the way options DTE math does)."""
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    current = start
    for close in closes:
        bars.append(
            Bar(symbol=pair, timestamp=current, open=close, high=close + spread, low=close - spread,
                close=close, volume=100.0)
        )
        current += timedelta(hours=1)
    return bars
