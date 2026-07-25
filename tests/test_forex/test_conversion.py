from unittest.mock import AsyncMock

import pytest

from forex.conversion import quote_to_account_rate


@pytest.mark.asyncio
async def test_returns_one_when_quote_matches_account_currency():
    broker = AsyncMock()

    rate = await quote_to_account_rate(broker, "EUR_USD", "USD", tradeable_pairs={"EUR_USD"})

    assert rate == 1.0
    broker.get_pricing.assert_not_awaited()


@pytest.mark.asyncio
async def test_uses_direct_conversion_pair_when_tradeable():
    broker = AsyncMock()
    broker.get_pricing.return_value = (0.0089, 0.0091)  # JPY_USD mid ~0.009

    rate = await quote_to_account_rate(broker, "EUR_JPY", "USD", tradeable_pairs={"EUR_JPY", "JPY_USD"})

    assert rate == pytest.approx(0.009)
    broker.get_pricing.assert_awaited_once_with("JPY_USD")


@pytest.mark.asyncio
async def test_uses_inverse_conversion_pair_when_direct_not_tradeable():
    broker = AsyncMock()
    broker.get_pricing.return_value = (110.0, 110.2)  # USD_JPY mid ~110.1

    rate = await quote_to_account_rate(broker, "EUR_JPY", "USD", tradeable_pairs={"EUR_JPY", "USD_JPY"})

    assert rate == pytest.approx(1 / 110.1)
    broker.get_pricing.assert_awaited_once_with("USD_JPY")


@pytest.mark.asyncio
async def test_returns_none_when_no_conversion_pair_tradeable():
    broker = AsyncMock()

    rate = await quote_to_account_rate(broker, "EUR_JPY", "USD", tradeable_pairs={"EUR_JPY"})

    assert rate is None
    broker.get_pricing.assert_not_awaited()
