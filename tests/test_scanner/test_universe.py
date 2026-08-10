from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from broker.models import ActiveSymbol, Quote
from scanner.optionable_repository import OptionableSymbolsRepository
from scanner.universe import UniverseManager
from scanner.universe_repository import UniverseRepository


def make_quote(symbol: str, ask_price: float) -> Quote:
    now = datetime.now(timezone.utc)
    return Quote(symbol=symbol, bid_price=ask_price, ask_price=ask_price, bid_size=1, ask_size=1, timestamp=now)


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
async def test_get_active_symbols_does_not_filter_by_optionable(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [
        ActiveSymbol(symbol="AAPL", volume=100),
        ActiveSymbol(symbol="PENNY", volume=90),
    ]
    manager = make_manager(pool, broker)

    symbols = await manager.get_active_symbols(now)

    assert symbols == ["AAPL", "PENNY"]
    broker.get_optionable_symbols.assert_not_awaited()


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
async def test_get_active_symbols_applies_price_ceiling(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [
        ActiveSymbol(symbol="CHEAP", volume=100),
        ActiveSymbol(symbol="PRICEY", volume=90),
    ]
    broker.get_latest_quote.side_effect = lambda symbol: make_quote(symbol, {"CHEAP": 10.0, "PRICEY": 900.0}[symbol])
    manager = make_manager(pool, broker)

    symbols = await manager.get_active_symbols(now, max_price=500.0)

    assert symbols == ["CHEAP"]


@pytest.mark.asyncio
async def test_get_universe_applies_price_ceiling(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [
        ActiveSymbol(symbol="CHEAP", volume=100),
        ActiveSymbol(symbol="PRICEY", volume=90),
    ]
    broker.get_optionable_symbols.return_value = ["CHEAP", "PRICEY"]
    broker.get_latest_quote.side_effect = lambda symbol: make_quote(symbol, {"CHEAP": 10.0, "PRICEY": 900.0}[symbol])
    manager = make_manager(pool, broker)

    symbols = await manager.get_universe(now, max_price=500.0)

    assert symbols == ["CHEAP"]


@pytest.mark.asyncio
async def test_get_active_symbols_no_ceiling_skips_quote_fetch(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="AAPL", volume=100)]
    manager = make_manager(pool, broker)

    symbols = await manager.get_active_symbols(now)

    assert symbols == ["AAPL"]
    broker.get_latest_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_active_symbols_excludes_symbol_with_failed_quote(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [
        ActiveSymbol(symbol="OK", volume=100),
        ActiveSymbol(symbol="BROKEN", volume=90),
    ]

    async def _quote(symbol):
        if symbol == "BROKEN":
            raise RuntimeError("quote fetch failed")
        return make_quote(symbol, 10.0)

    broker.get_latest_quote.side_effect = _quote
    manager = make_manager(pool, broker)

    symbols = await manager.get_active_symbols(now, max_price=500.0)

    assert symbols == ["OK"]


@pytest.mark.asyncio
async def test_price_cache_reused_within_ttl(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="AAPL", volume=100)]
    broker.get_latest_quote.return_value = make_quote("AAPL", 10.0)
    manager = make_manager(pool, broker, price_cache_ttl=timedelta(hours=1))

    await manager.get_active_symbols(now, max_price=500.0)
    await manager.get_active_symbols(now + timedelta(minutes=30), max_price=500.0)

    broker.get_latest_quote.assert_awaited_once()


@pytest.mark.asyncio
async def test_price_cache_refreshed_after_ttl(pool):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    broker = AsyncMock()
    broker.get_most_active_symbols.return_value = [ActiveSymbol(symbol="AAPL", volume=100)]
    broker.get_latest_quote.return_value = make_quote("AAPL", 10.0)
    manager = make_manager(pool, broker, price_cache_ttl=timedelta(hours=1))

    await manager.get_active_symbols(now, max_price=500.0)
    await manager.get_active_symbols(now + timedelta(hours=2), max_price=500.0)

    assert broker.get_latest_quote.await_count == 2


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
