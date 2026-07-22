from unittest.mock import AsyncMock

import pytest

from broker.models import Account, OrderSide, Position
from risk.halt_manager import HaltManager
from risk.pre_trade import PreTradeChecker


def make_account(equity=10000.0, buying_power=5000.0):
    return Account(account_id="acct-1", equity=equity, cash=equity, buying_power=buying_power, currency="USD")


def make_position(symbol="AAPL", market_value=1000.0):
    return Position(
        symbol=symbol, qty=1, side=OrderSide.BUY, avg_entry_price=100.0, market_value=market_value, unrealized_pl=0.0
    )


def make_checker(halted=False, **kwargs):
    halt_manager = AsyncMock(spec=HaltManager)
    halt_manager.is_halted.return_value = halted
    return PreTradeChecker(halt_manager, **kwargs)


@pytest.mark.asyncio
async def test_all_checks_pass_for_a_clean_trade():
    checker = make_checker()
    result = await checker.evaluate(make_account(), [], "AAPL", estimated_cost=500.0)

    assert result.passed is True
    assert all(c.passed for c in result.checks)


@pytest.mark.asyncio
async def test_fails_when_halted():
    checker = make_checker(halted=True)
    result = await checker.evaluate(make_account(), [], "AAPL", estimated_cost=500.0)

    assert result.passed is False
    halt_check = next(c for c in result.checks if c.name == "not_halted")
    assert halt_check.passed is False


@pytest.mark.asyncio
async def test_fails_when_cost_exceeds_buying_power():
    checker = make_checker()
    account = make_account(buying_power=100.0)

    result = await checker.evaluate(account, [], "AAPL", estimated_cost=500.0)

    assert result.passed is False
    check = next(c for c in result.checks if c.name == "buying_power")
    assert check.passed is False


@pytest.mark.asyncio
async def test_fails_when_open_position_cap_reached():
    checker = make_checker(max_open_positions=2)
    positions = [make_position("AAPL"), make_position("TSLA")]

    result = await checker.evaluate(make_account(), positions, "NVDA", estimated_cost=100.0)

    assert result.passed is False
    check = next(c for c in result.checks if c.name == "max_open_positions")
    assert check.passed is False


@pytest.mark.asyncio
async def test_fails_when_symbol_concentration_cap_reached():
    checker = make_checker(max_positions_per_symbol=1)
    positions = [make_position("AAPL")]

    result = await checker.evaluate(make_account(), positions, "AAPL", estimated_cost=100.0)

    assert result.passed is False
    check = next(c for c in result.checks if c.name == "symbol_concentration")
    assert check.passed is False


@pytest.mark.asyncio
async def test_allows_different_symbol_when_concentration_cap_reached():
    checker = make_checker(max_positions_per_symbol=1)
    positions = [make_position("AAPL")]

    result = await checker.evaluate(make_account(), positions, "TSLA", estimated_cost=100.0)

    check = next(c for c in result.checks if c.name == "symbol_concentration")
    assert check.passed is True


@pytest.mark.asyncio
async def test_fails_when_total_exposure_cap_exceeded():
    checker = make_checker(max_total_exposure_pct=0.5)
    account = make_account(equity=1000.0)
    positions = [make_position("AAPL", market_value=400.0)]

    result = await checker.evaluate(account, positions, "TSLA", estimated_cost=200.0)

    assert result.passed is False
    check = next(c for c in result.checks if c.name == "total_exposure")
    assert check.passed is False


@pytest.mark.asyncio
async def test_total_exposure_uses_absolute_market_value_for_short_positions():
    checker = make_checker(max_total_exposure_pct=0.5)
    account = make_account(equity=1000.0)
    positions = [make_position("AAPL", market_value=-400.0)]  # short position, negative market value

    result = await checker.evaluate(account, positions, "TSLA", estimated_cost=200.0)

    check = next(c for c in result.checks if c.name == "total_exposure")
    assert check.passed is False  # abs(-400) + 200 = 600 -> 60% > 50% cap


@pytest.mark.asyncio
async def test_multiple_failures_are_all_reported():
    checker = make_checker(halted=True, max_positions_per_symbol=1)
    account = make_account(buying_power=10.0)
    positions = [make_position("AAPL")]

    result = await checker.evaluate(account, positions, "AAPL", estimated_cost=500.0)

    failed_names = {c.name for c in result.checks if not c.passed}
    assert failed_names == {"not_halted", "buying_power", "symbol_concentration"}
