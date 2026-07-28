from datetime import datetime, timezone

import pytest

from decision_engine.models import TradeDirection
from decision_engine.signal_confirmation_repository import SignalConfirmationRepository


@pytest.mark.asyncio
async def test_get_returns_none_when_not_tracked(pool):
    repo = SignalConfirmationRepository(pool)
    assert await repo.get("AAPL", "stock", "1Day") is None


@pytest.mark.asyncio
async def test_upsert_and_get_roundtrip(pool):
    repo = SignalConfirmationRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.upsert("AAPL", "stock", "1Day", TradeDirection.BULLISH, 2, now)
    state = await repo.get("AAPL", "stock", "1Day")

    assert state.direction is TradeDirection.BULLISH
    assert state.streak == 2
    assert state.updated_at == now


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_streak(pool):
    repo = SignalConfirmationRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.upsert("AAPL", "stock", "1Day", TradeDirection.BULLISH, 1, now)
    await repo.upsert("AAPL", "stock", "1Day", TradeDirection.BULLISH, 2, now)

    state = await repo.get("AAPL", "stock", "1Day")
    assert state.streak == 2


@pytest.mark.asyncio
async def test_clear_removes_tracked_streak(pool):
    repo = SignalConfirmationRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert("AAPL", "stock", "1Day", TradeDirection.BULLISH, 2, now)

    await repo.clear("AAPL", "stock", "1Day")

    assert await repo.get("AAPL", "stock", "1Day") is None


@pytest.mark.asyncio
async def test_streaks_are_independent_per_vehicle_and_timeframe(pool):
    repo = SignalConfirmationRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.upsert("AAPL", "stock", "1Day", TradeDirection.BULLISH, 1, now)
    await repo.upsert("AAPL", "options", "1Day", TradeDirection.BEARISH, 2, now)
    await repo.upsert("AAPL", "options", "5Min", TradeDirection.BULLISH, 3, now)

    assert (await repo.get("AAPL", "stock", "1Day")).streak == 1
    assert (await repo.get("AAPL", "options", "1Day")).direction is TradeDirection.BEARISH
    assert (await repo.get("AAPL", "options", "5Min")).streak == 3
