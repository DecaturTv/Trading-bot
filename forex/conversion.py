import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class PricingSource(Protocol):
    async def get_pricing(self, pair: str) -> tuple[float, float]: ...


async def quote_to_account_rate(
    broker: PricingSource, pair: str, account_currency: str, tradeable_pairs: set[str]
) -> float | None:
    """Exchange rate to convert a price/stop-distance quoted in `pair`'s quote
    currency into account-currency terms.

    Returns 1.0 without any broker call when the pair is already quoted in
    the account currency (the common case: EUR_USD, GBP_USD, ... on a USD
    account). Otherwise looks for a tradeable conversion pair -- quote/account
    directly, or account/quote inverted -- and prices it. Returns None if no
    conversion pair is tradeable, so the caller can skip the trade rather than
    size it on a guessed rate.
    """
    quote_currency = pair.split("_")[1]
    if quote_currency == account_currency:
        return 1.0

    direct = f"{quote_currency}_{account_currency}"
    if direct in tradeable_pairs:
        bid, ask = await broker.get_pricing(direct)
        return (bid + ask) / 2

    inverse = f"{account_currency}_{quote_currency}"
    if inverse in tradeable_pairs:
        bid, ask = await broker.get_pricing(inverse)
        mid = (bid + ask) / 2
        if mid <= 0:
            return None
        return 1.0 / mid

    logger.warning(
        "no tradeable conversion pair for %s -> %s (needed to size %s correctly); skipping",
        quote_currency, account_currency, pair,
    )
    return None
