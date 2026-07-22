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
