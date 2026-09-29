from unittest.mock import AsyncMock

import pytest

from broker.models import Order, OrderSide, OrderStatus, OrderType
from execution.entry_confirmation import confirm_open_fill
from execution.executor import OrderTimeoutError


def _order(status, qty=10, filled_qty=0):
    return Order(
        order_id="o-1", symbol="AAPL", qty=qty, side=OrderSide.BUY, order_type=OrderType.LIMIT, status=status,
        filled_qty=filled_qty, filled_avg_price=None, submitted_at=None, filled_at=None,
    )


@pytest.mark.asyncio
async def test_full_fill_is_filled():
    executor, broker = AsyncMock(), AsyncMock()
    executor.await_fill.return_value = _order(OrderStatus.FILLED, filled_qty=10)

    result = await confirm_open_fill(executor, broker, "o-1", "AAPL")

    assert result.filled and result.filled_qty == 10


@pytest.mark.asyncio
async def test_expired_unfilled_entry_is_not_filled():
    """The ETHA/NVDL/ONDS/SOXS phantoms: DAY limit entry expired with nothing filled."""
    executor, broker = AsyncMock(), AsyncMock()
    executor.await_fill.return_value = _order(OrderStatus.EXPIRED)

    result = await confirm_open_fill(executor, broker, "o-1", "AAPL")

    assert not result.filled and result.filled_qty == 0


@pytest.mark.asyncio
async def test_canceled_with_partial_fill_tracks_the_filled_part():
    executor, broker = AsyncMock(), AsyncMock()
    executor.await_fill.return_value = _order(OrderStatus.CANCELED, filled_qty=4)

    result = await confirm_open_fill(executor, broker, "o-1", "AAPL")

    assert result.filled and result.filled_qty == 4


@pytest.mark.asyncio
async def test_timeout_cancels_then_picks_up_a_fill_that_landed_before_the_cancel():
    executor, broker = AsyncMock(), AsyncMock()
    executor.await_fill.side_effect = OrderTimeoutError("stuck")
    broker.get_order.return_value = _order(OrderStatus.PENDING_CANCEL, filled_qty=3)

    result = await confirm_open_fill(executor, broker, "o-1", "AAPL")

    broker.cancel_order.assert_awaited_once_with("o-1")
    assert result.filled and result.filled_qty == 3


@pytest.mark.asyncio
async def test_timeout_with_nothing_filled_is_not_filled():
    executor, broker = AsyncMock(), AsyncMock()
    executor.await_fill.side_effect = OrderTimeoutError("stuck")
    broker.get_order.return_value = _order(OrderStatus.CANCELED)

    result = await confirm_open_fill(executor, broker, "o-1", "AAPL")

    broker.cancel_order.assert_awaited_once_with("o-1")
    assert not result.filled
