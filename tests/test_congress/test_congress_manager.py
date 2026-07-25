from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from congress.manager import CongressTradeManager
from congress.models import CongressTrade, TransactionType


def make_trade(ticker="AAPL"):
    return CongressTrade(
        representative="Nancy Pelosi", chamber="house", ticker=ticker, transaction_type=TransactionType.BUY,
        transaction_date=date(2026, 7, 20), disclosure_date=date(2026, 7, 25), amount_mid=8000.0,
        filing_id="1", source_url="https://example.com/1.pdf",
    )


@pytest.mark.asyncio
async def test_get_recent_trades_syncs_when_never_synced_before():
    source = AsyncMock()
    source.fetch.return_value = [make_trade()]
    repository = AsyncMock()
    repository.latest_synced_at.return_value = None
    repository.get_recent_for_ticker.return_value = [make_trade()]
    manager = CongressTradeManager(source, repository)
    now = datetime.now(timezone.utc)

    trades = await manager.get_recent_trades("AAPL", now)

    source.fetch.assert_awaited_once()
    repository.upsert_many.assert_awaited_once()
    repository.record_synced_at.assert_awaited_once_with(now)
    assert trades == [make_trade()]


@pytest.mark.asyncio
async def test_get_recent_trades_skips_sync_when_recently_synced():
    source = AsyncMock()
    repository = AsyncMock()
    now = datetime.now(timezone.utc)
    repository.latest_synced_at.return_value = now - timedelta(hours=1)
    repository.get_recent_for_ticker.return_value = []
    manager = CongressTradeManager(source, repository, refresh_interval=timedelta(hours=6))

    await manager.get_recent_trades("AAPL", now)

    source.fetch.assert_not_awaited()
    repository.upsert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_recent_trades_resyncs_when_stale():
    source = AsyncMock()
    source.fetch.return_value = []
    repository = AsyncMock()
    now = datetime.now(timezone.utc)
    repository.latest_synced_at.return_value = now - timedelta(hours=7)
    repository.get_recent_for_ticker.return_value = []
    manager = CongressTradeManager(source, repository, refresh_interval=timedelta(hours=6))

    await manager.get_recent_trades("AAPL", now)

    source.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_recent_trades_queries_repository_with_lookback_window():
    source = AsyncMock()
    repository = AsyncMock()
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    repository.latest_synced_at.return_value = now
    repository.get_recent_for_ticker.return_value = []

    manager = CongressTradeManager(source, repository)
    await manager.get_recent_trades("AAPL", now, lookback_days=30)

    repository.get_recent_for_ticker.assert_awaited_once_with("AAPL", date(2026, 6, 25))
