from datetime import date, datetime, timezone

import pytest

from broker.models import OptionRight, OrderSide
from decision_engine.models import TradeDirection
from options.models import StrategyType
from trade_management.models import OpenPositionRecord, PersistedLeg, PositionState
from trade_management.position_state_repository import PositionStateRepository

EXPIRY = date(2026, 8, 21)
ENTRY_DATE = date(2026, 7, 1)


def make_record(symbol="AAPL", qty=4, entry_cost=500.0, scaled_out=False, peak_gain_pct=0.0):
    return OpenPositionRecord(
        symbol=symbol,
        strategy_type=StrategyType.LONG_CALL,
        direction=TradeDirection.BULLISH,
        entry_date=ENTRY_DATE,
        legs=[
            PersistedLeg(symbol=f"{symbol}260821C00150000", strike=150.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY)
        ],
        state=PositionState(symbol=symbol, qty=qty, entry_cost_per_unit=entry_cost, scaled_out=scaled_out, peak_gain_pct=peak_gain_pct),
    )


@pytest.mark.asyncio
async def test_get_returns_none_when_not_tracked(pool):
    repo = PositionStateRepository(pool)
    assert await repo.get("AAPL") is None


@pytest.mark.asyncio
async def test_upsert_and_get_roundtrip(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    record = make_record()

    await repo.upsert(record, updated_at=now)
    fetched = await repo.get("AAPL")

    assert fetched == record


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_record(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.upsert(make_record(qty=4, scaled_out=False, peak_gain_pct=0.0), updated_at=now)
    await repo.upsert(make_record(qty=2, scaled_out=True, peak_gain_pct=1.2), updated_at=now)

    fetched = await repo.get("AAPL")
    assert fetched.state.qty == 2
    assert fetched.state.scaled_out is True
    assert fetched.state.peak_gain_pct == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_delete_removes_tracked_record(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert(make_record(), updated_at=now)

    await repo.delete("AAPL")

    assert await repo.get("AAPL") is None


@pytest.mark.asyncio
async def test_positions_are_independent_per_symbol(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert(make_record(symbol="AAPL", qty=4), updated_at=now)
    await repo.upsert(make_record(symbol="TSLA", qty=2), updated_at=now)

    assert (await repo.get("AAPL")).state.qty == 4
    assert (await repo.get("TSLA")).state.qty == 2


@pytest.mark.asyncio
async def test_get_all_returns_every_tracked_position(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert(make_record(symbol="AAPL"), updated_at=now)
    await repo.upsert(make_record(symbol="TSLA"), updated_at=now)

    all_records = await repo.get_all()

    assert {r.symbol for r in all_records} == {"AAPL", "TSLA"}


@pytest.mark.asyncio
async def test_get_all_empty_when_no_positions_tracked(pool):
    repo = PositionStateRepository(pool)
    assert await repo.get_all() == []


@pytest.mark.asyncio
async def test_legs_round_trip_correctly(pool):
    repo = PositionStateRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    record = make_record()

    await repo.upsert(record, updated_at=now)
    fetched = await repo.get("AAPL")

    assert len(fetched.legs) == 1
    leg = fetched.legs[0]
    assert leg.symbol == "AAPL260821C00150000"
    assert leg.strike == pytest.approx(150.0)
    assert leg.expiration == EXPIRY
    assert leg.right is OptionRight.CALL
    assert leg.side is OrderSide.BUY
    assert fetched.strategy_type is StrategyType.LONG_CALL
    assert fetched.direction is TradeDirection.BULLISH
    assert fetched.entry_date == ENTRY_DATE
