from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from broker.models import ActiveSymbol
from scanner.optionable_repository import OptionableSymbolsRepository
from scanner.universe import UniverseManager
from scanner.universe_repository import UniverseRepository


def make_manager(pool, broker, **overrides):
    return UniverseManager(broker, UniverseRepository(pool), OptionableSymbolsRepository(pool), **overrides)


@pytest.mark.asyncio
async def test_get_universe_refreshes_both_caches_when_empty(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="AAPL", volume=100)]
    broker.get_optionable_symbols.return_value = ["AAPL"]
    manager = make_manager(pool, broker, size=1)

    symbols = await manager.get_universe(now)

    assert symbols == ["AAPL"]
    broker.get_most_active_symbols.assert_awaited_once_with(top=1)
    broker.get_optionable_symbols.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_universe_excludes_active_symbols_without_options(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [
        ActiveSymbol(symbol="AAPL", volume=100),
        ActiveSymbol(symbol="PENNY", volume=90),
    ]
    broker.get_optionable_symbols.return_value = ["AAPL"]  # PENNY has no listed options
    manager = make_manager(pool, broker)

    symbols = await manager.get_universe(now)

    assert symbols == ["AAPL"]


@pytest.mark.asyncio
async def test_get_universe_preserves_volume_rank_order(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [
        ActiveSymbol(symbol="AAPL", volume=100),
        ActiveSymbol(symbol="TSLA", volume=90),
        ActiveSymbol(symbol="NVDA", volume=80),
    ]
    broker.get_optionable_symbols.return_value = ["NVDA", "AAPL", "TSLA"]  # unordered
    manager = make_manager(pool, broker)

    symbols = await manager.get_universe(now)

    assert symbols == ["AAPL", "TSLA", "NVDA"]  # rank order, not optionable-list order


@pytest.mark.asyncio
async def test_get_universe_uses_cached_snapshots_within_refresh_interval(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    universe_repo = UniverseRepository(pool)
    optionable_repo = OptionableSymbolsRepository(pool)
    await universe_repo.save_snapshot(now - timedelta(days=1), [ActiveSymbol(symbol="CACHED", volume=1)])
    await optionable_repo.save_snapshot(now - timedelta(days=1), ["CACHED"])

    broker = AsyncMock()
    manager = UniverseManager(broker, universe_repo, optionable_repo, refresh_interval=timedelta(days=7))

    symbols = await manager.get_universe(now)

    assert symbols == ["CACHED"]
    broker.get_most_active_symbols.assert_not_awaited()
    broker.get_optionable_symbols.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_universe_refreshes_when_snapshot_is_stale(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    universe_repo = UniverseRepository(pool)
    optionable_repo = OptionableSymbolsRepository(pool)
    await universe_repo.save_snapshot(now - timedelta(days=10), [ActiveSymbol(symbol="STALE", volume=1)])
    await optionable_repo.save_snapshot(now - timedelta(days=10), ["STALE"])

    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="FRESH", volume=1)]
    broker.get_optionable_symbols.return_value = ["FRESH"]
    manager = UniverseManager(broker, universe_repo, optionable_repo, refresh_interval=timedelta(days=7))

    symbols = await manager.get_universe(now)

    assert symbols == ["FRESH"]
    broker.get_most_active_symbols.assert_awaited_once()
    broker.get_optionable_symbols.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_persists_both_snapshots(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    universe_repo = UniverseRepository(pool)
    optionable_repo = OptionableSymbolsRepository(pool)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="AAPL", volume=100)]
    broker.get_optionable_symbols.return_value = ["AAPL"]
    manager = UniverseManager(broker, universe_repo, optionable_repo)

    result = await manager.refresh(now)

    assert result == ["AAPL"]
    assert await universe_repo.latest_snapshot() == (now, ["AAPL"])
    assert await optionable_repo.latest_snapshot() == (now, ["AAPL"])
