from datetime import datetime, timezone

import pytest

from broker.models import OrderSide
from forex.models import OpenForexPosition
from forex.position_repository import ForexPositionRepository


def make_position(pair="EUR_USD", oanda_trade_id="1"):
    return OpenForexPosition(
        pair=pair, side=OrderSide.BUY, units=1000, entry_price=1.1000, stop_loss_price=1.0950,
        take_profit_price=1.1100, oanda_trade_id=oanda_trade_id, opened_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_get_returns_none_when_absent(pool):
    repo = ForexPositionRepository(pool)
    assert await repo.get("EUR_USD") is None


@pytest.mark.asyncio
async def test_upsert_then_get_roundtrips(pool):
    repo = ForexPositionRepository(pool)
    position = make_position()

    await repo.upsert(position)
    fetched = await repo.get("EUR_USD")

    assert fetched == position


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_pair(pool):
    repo = ForexPositionRepository(pool)
    await repo.upsert(make_position(oanda_trade_id="1"))
    await repo.upsert(make_position(oanda_trade_id="2"))

    fetched = await repo.get("EUR_USD")

    assert fetched.oanda_trade_id == "2"


@pytest.mark.asyncio
async def test_get_all_returns_all_tracked_pairs(pool):
    repo = ForexPositionRepository(pool)
    await repo.upsert(make_position(pair="EUR_USD"))
    await repo.upsert(make_position(pair="GBP_USD"))

    all_positions = await repo.get_all()

    assert {p.pair for p in all_positions} == {"EUR_USD", "GBP_USD"}


@pytest.mark.asyncio
async def test_delete_removes_position(pool):
    repo = ForexPositionRepository(pool)
    await repo.upsert(make_position())

    await repo.delete("EUR_USD")

    assert await repo.get("EUR_USD") is None
