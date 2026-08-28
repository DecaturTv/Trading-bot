from datetime import date, datetime, timedelta, timezone

import pytest

from broker.models import Bar, OptionContract, OptionRight
from data.option_bars_repository import OptionBarsRepository

OCC = "AAPL260918C00200000"


def make_bar(occ, ts, close=5.0):
    return Bar(symbol=occ, timestamp=ts, open=close - 0.1, high=close + 0.1, low=close - 0.2, close=close, volume=10)


def make_contract(occ=OCC, underlying="AAPL", strike=200.0, expiration=date(2026, 9, 18), right=OptionRight.CALL):
    return OptionContract(
        symbol=occ, underlying_symbol=underlying, strike=strike, expiration=expiration, right=right,
        bid=None, ask=None, last_price=None, implied_volatility=None, greeks=None,
    )


@pytest.mark.asyncio
async def test_option_bars_roundtrip_and_idempotent_upsert(pool):
    repo = OptionBarsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.upsert_option_bars("15Min", [make_bar(OCC, now, close=5.0)])
    await repo.upsert_option_bars("15Min", [make_bar(OCC, now, close=6.0)])  # conflict -> update

    fetched = await repo.get_option_bars(OCC, "15Min", now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(fetched) == 1
    assert fetched[0].close == 6.0
    assert await repo.latest_timestamp(OCC, "15Min") == now
    assert await repo.latest_timestamp("MISSING", "15Min") is None


@pytest.mark.asyncio
async def test_contracts_upsert_and_lookup_by_underlying(pool):
    repo = OptionBarsRepository(pool)
    call = make_contract(strike=200.0, right=OptionRight.CALL)
    put = make_contract(occ="AAPL260918P00180000", strike=180.0, right=OptionRight.PUT)
    other_exp = make_contract(occ="AAPL261218C00200000", strike=200.0, expiration=date(2026, 12, 18))
    await repo.upsert_contracts([call, put, other_exp])
    await repo.upsert_contracts([call])  # ON CONFLICT DO NOTHING

    got = await repo.get_contracts("AAPL", date(2026, 9, 1), date(2026, 9, 30))
    assert {c.symbol for c in got} == {call.symbol, put.symbol}
    assert {c.right for c in got} == {OptionRight.CALL, OptionRight.PUT}


@pytest.mark.asyncio
async def test_get_contracts_with_bars_only_returns_priced_contracts(pool):
    repo = OptionBarsRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    priced = make_contract(occ="AAPL260918C00200000", strike=200.0)
    unpriced = make_contract(occ="AAPL260918C00210000", strike=210.0)
    await repo.upsert_contracts([priced, unpriced])
    await repo.upsert_option_bars("15Min", [make_bar(priced.symbol, now)])

    with_bars = await repo.get_contracts_with_bars(
        "AAPL", "15Min", date(2026, 9, 1), date(2026, 9, 30), now - timedelta(days=1), now + timedelta(days=1)
    )
    assert [c.symbol for c in with_bars] == [priced.symbol]
    assert await repo.bar_count_for_underlying("AAPL", "15Min") == 1
