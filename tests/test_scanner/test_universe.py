from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from broker.models import ActiveSymbol
from scanner.universe import UniverseManager
from scanner.universe_repository import UniverseRepository


@pytest.mark.asyncio
async def test_get_universe_refreshes_when_no_snapshot_exists(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="AAPL", volume=100)]
    manager = UniverseManager(broker, UniverseRepository(pool), size=1)

    symbols = await manager.get_universe(now)

    assert symbols == ["AAPL"]
    broker.get_most_active_symbols.assert_awaited_once_with(top=1)


@pytest.mark.asyncio
async def test_get_universe_uses_cached_snapshot_within_refresh_interval(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    repo = UniverseRepository(pool)
    await repo.save_snapshot(now - timedelta(days=1), [ActiveSymbol(symbol="CACHED", volume=1)])

    broker = AsyncMock()
    manager = UniverseManager(broker, repo, refresh_interval=timedelta(days=7))

    symbols = await manager.get_universe(now)

    assert symbols == ["CACHED"]
    broker.get_most_active_symbols.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_universe_refreshes_when_snapshot_is_stale(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    repo = UniverseRepository(pool)
    await repo.save_snapshot(now - timedelta(days=10), [ActiveSymbol(symbol="STALE", volume=1)])

    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="FRESH", volume=1)]
    manager = UniverseManager(broker, repo, refresh_interval=timedelta(days=7))

    symbols = await manager.get_universe(now)

    assert symbols == ["FRESH"]
    broker.get_most_active_symbols.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_persists_snapshot(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    repo = UniverseRepository(pool)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="AAPL", volume=100)]
    manager = UniverseManager(broker, repo)

    await manager.refresh(now)

    assert await repo.latest_snapshot() == (now, ["AAPL"])
