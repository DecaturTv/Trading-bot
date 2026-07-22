from datetime import date, timedelta


def trading_days_until(expiration: date, as_of: date) -> int:
    """Counts weekdays (Mon-Fri) strictly between as_of and expiration (exclusive
    of as_of, inclusive of expiration if it's a weekday).

    Does not account for market holidays — same known limitation as
    utils/time.py's is_equity_market_open; plug in a holiday calendar before
    relying on this for exact scheduling.
    """
    if expiration <= as_of:
        return 0
    count = 0
    current = as_of
    while current < expiration:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count
