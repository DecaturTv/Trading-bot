import math
from collections.abc import Sequence

from broker.models import Bar


def realized_volatility(bars: Sequence[Bar], lookback: int = 20, trading_days_per_year: int = 252) -> float | None:
    """Annualized close-to-close realized volatility over the trailing lookback
    bars — used as an IV proxy when simulating option pricing against
    historical underlying data, since this project has no historical options
    IV to draw on. Returns None if there isn't enough history yet.
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if len(bars) < lookback + 1:
        return None
    closes = [b.close for b in bars[-(lookback + 1) :]]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(variance)
    return daily_vol * math.sqrt(trading_days_per_year)
