from datetime import datetime, timedelta, timezone

import pytest

from broker.models import Bar
from data.bars_repository import BarsRepository


def make_bar(symbol, ts, close=100.0):
    return Bar(symbol=symbol, timestamp=ts, open=close - 1, high=close + 1, low=close - 2, close=close, volume=1000)


@pytest.mark.asyncio
async def test_upsert_and_get_bars_roundtrip(pool):
    repo = BarsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bars = [make_bar("AAPL", now - timedelta(minutes=i)) for i in range(3)]

    stored = await repo.upsert_bars("1Min", bars)
    fetched = await repo.get_bars("AAPL", "1Min", now - timedelta(hours=1), now + timedelta(hours=1))

    assert stored == 3
    assert len(fetched) == 3
    assert [b.timestamp for b in fetched] == sorted(b.timestamp for b in fetched)


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_updates_on_conflict(pool):
    repo = BarsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.upsert_bars("1Min", [make_bar("AAPL", now, close=100.0)])
    await repo.upsert_bars("1Min", [make_bar("AAPL", now, close=105.0)])

    fetched = await repo.get_bars("AAPL", "1Min", now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert len(fetched) == 1
    assert fetched[0].close == 105.0


@pytest.mark.asyncio
async def test_upsert_empty_list_is_noop(pool):
    repo = BarsRepository(pool)
    assert await repo.upsert_bars("1Min", []) == 0


@pytest.mark.asyncio
async def test_latest_timestamp_returns_none_when_no_data(pool):
    repo = BarsRepository(pool)
    assert await repo.latest_timestamp("AAPL", "1Min") is None


@pytest.mark.asyncio
async def test_latest_timestamp_returns_max(pool):
    repo = BarsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bars = [make_bar("AAPL", now - timedelta(minutes=i)) for i in range(3)]
    await repo.upsert_bars("1Min", bars)

    assert await repo.latest_timestamp("AAPL", "1Min") == now


@pytest.mark.asyncio
async def test_bars_are_scoped_by_timeframe(pool):
    repo = BarsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert_bars("1Min", [make_bar("AAPL", now)])
    await repo.upsert_bars("1Day", [make_bar("AAPL", now)])

    minute_bars = await repo.get_bars("AAPL", "1Min", now - timedelta(minutes=1), now + timedelta(minutes=1))
    day_bars = await repo.get_bars("AAPL", "1Day", now - timedelta(minutes=1), now + timedelta(minutes=1))

    assert len(minute_bars) == 1
    assert len(day_bars) == 1


@pytest.mark.asyncio
async def test_bars_are_scoped_by_symbol(pool):
    repo = BarsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert_bars("1Min", [make_bar("AAPL", now), make_bar("TSLA", now)])

    aapl_bars = await repo.get_bars("AAPL", "1Min", now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert len(aapl_bars) == 1
    assert aapl_bars[0].symbol == "AAPL"
