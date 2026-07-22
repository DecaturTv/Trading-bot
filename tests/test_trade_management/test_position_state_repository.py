from datetime import datetime, timezone

import pytest

from trade_management.models import PositionState
from trade_management.position_state_repository import PositionStateRepository


@pytest.mark.asyncio
async def test_get_returns_none_when_not_tracked(pool):
    repo = PositionStateRepository(pool)
    assert await repo.get("AAPL") is None


@pytest.mark.asyncio
async def test_upsert_and_get_roundtrip(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = PositionState(symbol="AAPL", qty=4, entry_cost_per_unit=500.0, scaled_out=False, peak_gain_pct=0.0)

    await repo.upsert(state, updated_at=now)
    fetched = await repo.get("AAPL")

    assert fetched == state


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_state(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.upsert(PositionState("AAPL", 4, 500.0, False, 0.0), updated_at=now)
    await repo.upsert(PositionState("AAPL", 2, 500.0, True, 1.2), updated_at=now)

    fetched = await repo.get("AAPL")
    assert fetched.qty == 2
    assert fetched.scaled_out is True
    assert fetched.peak_gain_pct == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_delete_removes_tracked_state(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert(PositionState("AAPL", 4, 500.0, False, 0.0), updated_at=now)

    await repo.delete("AAPL")

    assert await repo.get("AAPL") is None


@pytest.mark.asyncio
async def test_positions_are_independent_per_symbol(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert(PositionState("AAPL", 4, 500.0, False, 0.0), updated_at=now)
    await repo.upsert(PositionState("TSLA", 2, 300.0, True, 0.5), updated_at=now)

    assert (await repo.get("AAPL")).qty == 4
    assert (await repo.get("TSLA")).qty == 2
