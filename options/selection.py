from collections.abc import Sequence
from datetime import date

from broker.models import OptionContract


def select_expiration(expirations: Sequence[date], target_dte: int, as_of: date) -> date:
    """Picks the expiration whose days-to-expiry is closest to target_dte."""
    if not expirations:
        raise ValueError("expirations must not be empty")
    return min(expirations, key=lambda exp: abs((exp - as_of).days - target_dte))


def select_strike_by_delta(contracts: Sequence[OptionContract], target_delta: float) -> OptionContract:
    """Picks the contract whose |delta| is closest to |target_delta|.

    Comparing absolute values lets callers pass either sign convention
    (calls are positive, puts negative) without needing to know which.
    """
    candidates = [c for c in contracts if c.greeks is not None and c.greeks.delta is not None]
    if not candidates:
        raise ValueError("no contracts with delta data available")
    return min(candidates, key=lambda c: abs(abs(c.greeks.delta) - abs(target_delta)))
