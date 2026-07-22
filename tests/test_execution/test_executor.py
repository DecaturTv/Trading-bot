from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from option_factories import make_contract

from broker.models import Order, OrderSide, OrderStatus, OrderType
from execution.executor import OrderExecutor, OrderTimeoutError
from options.strategy_builders import build_debit_vertical_spread, build_long_call

NEAR = date(2026, 8, 21)


def make_order(order_id="order-1", status=OrderStatus.NEW, symbol="C1"):
    now = datetime.now(timezone.utc)
    return Order(
        order_id=order_id,
        symbol=symbol,
        qty=1,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        status=status,
        filled_qty=0,
        filled_avg_price=None,
        submitted_at=now,
        filled_at=None,
    )


@pytest.mark.asyncio
async def test_execute_submits_single_leg_order_via_submit_order():
    broker = AsyncMock()
    broker.submit_order.return_value = make_order()
    executor = OrderExecutor(broker)
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5, underlying_symbol="AAPL")
    strategy = build_long_call(contract)

    result = await executor.execute(strategy, qty=2)

    assert result.order.order_id == "order-1"
    assert result.symbol == "AAPL"
    assert result.strategy_type == strategy.strategy_type
    broker.submit_order.assert_awaited_once()
    broker.submit_multi_leg_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_submits_multi_leg_order_via_submit_multi_leg_order():
    broker = AsyncMock()
    broker.submit_multi_leg_order.return_value = make_order(order_id="combo-1")
    executor = OrderExecutor(broker)
    long_contract = make_contract("LONG", 100, NEAR, ask=5.0, delta=0.5, underlying_symbol="AAPL")
    short_contract = make_contract("SHORT", 105, NEAR, bid=2.0, delta=0.3, underlying_symbol="AAPL")
    strategy = build_debit_vertical_spread(long_contract, short_contract)

    result = await executor.execute(strategy, qty=1)

    assert result.order.order_id == "combo-1"
    assert result.symbol == "AAPL"
    broker.submit_multi_leg_order.assert_awaited_once()
    broker.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_await_fill_returns_once_terminal(monkeypatch):
    import asyncio as asyncio_module

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio_module, "sleep", no_sleep)

    broker = AsyncMock()
    broker.get_order.side_effect = [
        make_order(status=OrderStatus.NEW),
        make_order(status=OrderStatus.PARTIALLY_FILLED),
        make_order(status=OrderStatus.FILLED),
    ]
    executor = OrderExecutor(broker)

    order = await executor.await_fill("order-1", poll_interval=0.01, max_attempts=10)

    assert order.status is OrderStatus.FILLED
    assert broker.get_order.await_count == 3


@pytest.mark.asyncio
async def test_await_fill_raises_after_max_attempts(monkeypatch):
    import asyncio as asyncio_module

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio_module, "sleep", no_sleep)

    broker = AsyncMock()
    broker.get_order.return_value = make_order(status=OrderStatus.NEW)
    executor = OrderExecutor(broker)

    with pytest.raises(OrderTimeoutError):
        await executor.await_fill("order-1", poll_interval=0.01, max_attempts=3)

    assert broker.get_order.await_count == 3
