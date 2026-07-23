from datetime import datetime, timezone

import pytest

from scanner.optionable_repository import OptionableSymbolsRepository


@pytest.mark.asyncio
async def test_latest_snapshot_returns_none_when_empty(pool):
    repo = OptionableSymbolsRepository(pool)
    assert await repo.latest_snapshot() is None


@pytest.mark.asyncio
async def test_save_and_retrieve_snapshot(pool):
    repo = OptionableSymbolsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.save_snapshot(now, ["AAPL", "TSLA", "NVDA"])
    result = await repo.latest_snapshot()

    assert result == (now, ["AAPL", "NVDA", "TSLA"])  # alphabetical, not insertion order


@pytest.mark.asyncio
async def test_latest_snapshot_returns_most_recent_computed_at(pool):
    repo = OptionableSymbolsRepository(pool)
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 1, 8, tzinfo=timezone.utc)

    await repo.save_snapshot(older, ["OLD"])
    await repo.save_snapshot(newer, ["NEW"])

    computed_at, symbols = await repo.latest_snapshot()
    assert computed_at == newer
    assert symbols == ["NEW"]
