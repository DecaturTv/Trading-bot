from datetime import date
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from congress.models import TransactionType
from congress.source import HouseStockWatcherSource

RAW_ROW = {
    "transaction_date": "07/20/2026",
    "disclosure_date": "07/25/2026",
    "ticker": "AAPL",
    "asset_description": "Apple Inc.",
    "asset_type": "Stock",
    "type": "Purchase",
    "amount": "$1,001 - $15,000",
    "amount_mid": 8000,
    "representative": "Nancy Pelosi",
    "district": "CA11",
    "owner": "Joint",
    "filing_id": "12345",
    "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/12345.pdf",
}


def make_source(rows):
    client = AsyncMock()
    response = MagicMock()
    response.json.return_value = rows
    response.raise_for_status = MagicMock()
    client.get.return_value = response
    return HouseStockWatcherSource(http_client=client)


@pytest.mark.asyncio
async def test_fetch_parses_a_clean_row():
    source = make_source([RAW_ROW])

    trades = await source.fetch()

    assert len(trades) == 1
    trade = trades[0]
    assert trade.representative == "Nancy Pelosi"
    assert trade.ticker == "AAPL"
    assert trade.transaction_type is TransactionType.BUY
    assert trade.transaction_date == date(2026, 7, 20)
    assert trade.disclosure_date == date(2026, 7, 25)
    assert trade.amount_mid == 8000.0
    assert trade.filing_id == "12345"


@pytest.mark.asyncio
async def test_fetch_maps_sale_to_sell():
    row = {**RAW_ROW, "type": "Sale"}
    trades = await make_source([row]).fetch()
    assert trades[0].transaction_type is TransactionType.SELL


@pytest.mark.asyncio
async def test_fetch_skips_exchange_type():
    row = {**RAW_ROW, "type": "Exchange"}
    trades = await make_source([row]).fetch()
    assert trades == []


@pytest.mark.asyncio
async def test_fetch_skips_row_with_blank_ticker():
    row = {**RAW_ROW, "ticker": ""}
    trades = await make_source([row]).fetch()
    assert trades == []


@pytest.mark.asyncio
async def test_fetch_skips_row_with_non_ticker_symbol():
    row = {**RAW_ROW, "ticker": "N/A"}
    trades = await make_source([row]).fetch()
    assert trades == []


@pytest.mark.asyncio
async def test_fetch_skips_row_with_unparseable_date():
    row = {**RAW_ROW, "transaction_date": "garbled"}
    trades = await make_source([row]).fetch()
    assert trades == []


@pytest.mark.asyncio
async def test_fetch_continues_past_one_bad_row():
    trades = await make_source([{**RAW_ROW, "ticker": ""}, RAW_ROW]).fetch()
    assert len(trades) == 1


@pytest.mark.asyncio
async def test_fetch_raises_on_http_error():
    client = AsyncMock()
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock())
    client.get.return_value = response
    source = HouseStockWatcherSource(http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await source.fetch()
