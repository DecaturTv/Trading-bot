from datetime import datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

_EQUITY_OPEN = dt_time(9, 30)
_EQUITY_CLOSE = dt_time(16, 0)
_FOREX_WEEKEND_BOUNDARY = dt_time(17, 0)


def now_eastern() -> datetime:
    return datetime.now(EASTERN)


def is_equity_market_open(moment: datetime | None = None) -> bool:
    """US equities/options regular session, Mon-Fri 9:30-16:00 ET.

    Does not account for market holidays — plug in a holiday calendar
    before relying on this for real scheduling decisions.
    """
    moment = (moment or now_eastern()).astimezone(EASTERN)
    if moment.weekday() >= 5:
        return False
    return _EQUITY_OPEN <= moment.time() < _EQUITY_CLOSE


def is_forex_market_open(moment: datetime | None = None) -> bool:
    """Forex trades ~24/5: Sun 17:00 ET to Fri 17:00 ET."""
    moment = (moment or now_eastern()).astimezone(EASTERN)
    weekday = moment.weekday()  # Mon=0 ... Sun=6
    if weekday == 5:
        return False
    if weekday == 6:
        return moment.time() >= _FOREX_WEEKEND_BOUNDARY
    if weekday == 4:
        return moment.time() < _FOREX_WEEKEND_BOUNDARY
    return True
