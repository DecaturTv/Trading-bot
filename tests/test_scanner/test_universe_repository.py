from datetime import datetime, timezone

import pytest

from broker.models import ActiveSymbol
from scanner.universe_repository import UniverseRepository


@pytest.mark.asyncio
async def test_latest_snapshot_returns_none_when_empty(pool):
    repo = UniverseRepository(pool)
    assert await repo.latest_snapshot() is None


@pytest.mark.asyncio
async def test_save_and_retrieve_snapshot_preserves_rank_order(pool):
    repo = UniverseRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    symbols = [
        ActiveSymbol(symbol="AAPL", volume=100),
        ActiveSymbol(symbol="TSLA", volume=90),
        ActiveSymbol(symbol="NVDA", volume=80),
    ]

    await repo.save_snapshot(now, symbols)
    result = await repo.latest_snapshot()

    assert result == (now, ["AAPL", "TSLA", "NVDA"])


@pytest.mark.asyncio
async def test_latest_snapshot_returns_most_recent_computed_at(pool):
    repo = UniverseRepository(pool)
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 1, 8, tzinfo=timezone.utc)

    await repo.save_snapshot(older, [ActiveSymbol(symbol="OLD", volume=1)])
    await repo.save_snapshot(newer, [ActiveSymbol(symbol="NEW", volume=1)])

    computed_at, symbols = await repo.latest_snapshot()
    assert computed_at == newer
    assert symbols == ["NEW"]
