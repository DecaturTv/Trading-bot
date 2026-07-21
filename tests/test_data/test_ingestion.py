from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from broker.models import Bar
from data.bars_repository import BarsRepository
from data.ingestion import BarIngestionService


def make_bar(symbol, ts, close=100.0):
    return Bar(symbol=symbol, timestamp=ts, open=close - 1, high=close + 1, low=close - 2, close=close, volume=1000)


@pytest.mark.asyncio
async def test_ingest_stores_bars_from_broker(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_bars.return_value = [make_bar("AAPL", now - timedelta(minutes=i)) for i in range(2)]
    service = BarIngestionService(broker, BarsRepository(pool))

    stored = await service.ingest("AAPL", "1Min", now - timedelta(hours=1), now)

    assert stored == 2
    broker.get_bars.assert_awaited_once_with("AAPL", "1Min", now - timedelta(hours=1), now)


@pytest.mark.asyncio
async def test_backfill_ingests_each_symbol(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_bars.side_effect = lambda symbol, tf, start, end: [make_bar(symbol, now)]
    service = BarIngestionService(broker, BarsRepository(pool))

    results = await service.backfill(["AAPL", "TSLA"], "1Day", now - timedelta(days=1), now)

    assert results == {"AAPL": 1, "TSLA": 1}
    assert broker.get_bars.await_count == 2


@pytest.mark.asyncio
async def test_ingest_incremental_starts_after_latest_stored_bar(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    repo = BarsRepository(pool)
    await repo.upsert_bars("1Min", [make_bar("AAPL", now - timedelta(minutes=10))])

    broker = AsyncMock()
    broker.get_bars.return_value = []
    service = BarIngestionService(broker, repo)

    await service.ingest_incremental("AAPL", "1Min", end=now)

    called_start = broker.get_bars.await_args.args[2]
    assert called_start == now - timedelta(minutes=10) + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_ingest_incremental_uses_default_lookback_when_no_data(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_bars.return_value = []
    service = BarIngestionService(broker, BarsRepository(pool))

    await service.ingest_incremental("AAPL", "1Min", end=now, default_lookback=timedelta(days=5))

    called_start = broker.get_bars.await_args.args[2]
    assert called_start == now - timedelta(days=5)


@pytest.mark.asyncio
async def test_ingest_incremental_skips_when_already_current(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    repo = BarsRepository(pool)
    await repo.upsert_bars("1Min", [make_bar("AAPL", now)])

    broker = AsyncMock()
    service = BarIngestionService(broker, repo)

    stored = await service.ingest_incremental("AAPL", "1Min", end=now)

    assert stored == 0
    broker.get_bars.assert_not_awaited()
