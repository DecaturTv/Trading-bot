from datetime import date, datetime, timedelta, timezone

import pytest

from congress.models import CongressTrade, TransactionType
from congress.repository import CongressTradeRepository


def make_trade(
    ticker="AAPL", representative="Nancy Pelosi", transaction_type=TransactionType.BUY,
    transaction_date=date(2026, 7, 20), disclosure_date=date(2026, 7, 25), amount_mid=8000.0,
    filing_id="1",
):
    return CongressTrade(
        representative=representative, chamber="house", ticker=ticker, transaction_type=transaction_type,
        transaction_date=transaction_date, disclosure_date=disclosure_date, amount_mid=amount_mid,
        filing_id=filing_id, source_url="https://example.com/1.pdf",
    )


@pytest.mark.asyncio
async def test_get_recent_for_ticker_returns_empty_when_none_stored(pool):
    repo = CongressTradeRepository(pool)
    assert await repo.get_recent_for_ticker("AAPL", date(2026, 1, 1)) == []


@pytest.mark.asyncio
async def test_upsert_then_get_recent_roundtrips(pool):
    repo = CongressTradeRepository(pool)
    trade = make_trade()

    await repo.upsert_many([trade])
    fetched = await repo.get_recent_for_ticker("AAPL", date(2026, 1, 1))

    assert fetched == [trade]


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_same_natural_key(pool):
    repo = CongressTradeRepository(pool)
    trade = make_trade()

    await repo.upsert_many([trade])
    await repo.upsert_many([trade])  # duplicate sync shouldn't double-insert

    fetched = await repo.get_recent_for_ticker("AAPL", date(2026, 1, 1))
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_get_recent_for_ticker_filters_by_disclosure_date(pool):
    repo = CongressTradeRepository(pool)
    old_trade = make_trade(filing_id="1", disclosure_date=date(2026, 1, 1))
    recent_trade = make_trade(filing_id="2", disclosure_date=date(2026, 7, 20))
    await repo.upsert_many([old_trade, recent_trade])

    fetched = await repo.get_recent_for_ticker("AAPL", since=date(2026, 6, 1))

    assert fetched == [recent_trade]


@pytest.mark.asyncio
async def test_get_recent_for_ticker_filters_by_ticker(pool):
    repo = CongressTradeRepository(pool)
    await repo.upsert_many([make_trade(ticker="AAPL", filing_id="1"), make_trade(ticker="MSFT", filing_id="2")])

    fetched = await repo.get_recent_for_ticker("AAPL", date(2026, 1, 1))

    assert [t.ticker for t in fetched] == ["AAPL"]


@pytest.mark.asyncio
async def test_latest_synced_at_is_none_before_first_sync(pool):
    repo = CongressTradeRepository(pool)
    assert await repo.latest_synced_at() is None


@pytest.mark.asyncio
async def test_record_synced_at_then_latest_synced_at_roundtrips(pool):
    repo = CongressTradeRepository(pool)
    now = datetime.now(timezone.utc)

    await repo.record_synced_at(now)
    fetched = await repo.latest_synced_at()

    assert abs((fetched - now).total_seconds()) < 1


@pytest.mark.asyncio
async def test_record_synced_at_overwrites_previous_value(pool):
    repo = CongressTradeRepository(pool)
    first = datetime.now(timezone.utc) - timedelta(hours=1)
    second = datetime.now(timezone.utc)

    await repo.record_synced_at(first)
    await repo.record_synced_at(second)
    fetched = await repo.latest_synced_at()

    assert abs((fetched - second).total_seconds()) < 1
