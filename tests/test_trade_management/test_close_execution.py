from datetime import date
from unittest.mock import AsyncMock

import pytest

from broker.models import OptionContract, OptionRight, Order, OrderSide, OrderStatus, OrderType
from options.models import OptionLeg, OptionStrategy, StrategyType
from trade_management.close_execution import close_with_price_walk

CONTRACT = OptionContract(
    symbol="AAPL261016C00150000", underlying_symbol="AAPL", strike=150.0, expiration=date(2026, 10, 16),
    right=OptionRight.CALL, bid=0.20, ask=0.60, last_price=0.4, implied_volatility=0.3, greeks=None,
)
STRATEGY = OptionStrategy(
    strategy_type=StrategyType.LONG_CALL, legs=[OptionLeg(contract=CONTRACT, side=OrderSide.BUY)],
    net_debit=50.0, max_loss=50.0, max_gain=None, net_delta=0.0,
)


def _order(status, filled_qty):
    return Order(
        order_id="o1", symbol=CONTRACT.symbol, qty=2, side=OrderSide.SELL, order_type=OrderType.LIMIT,
        status=status, filled_qty=filled_qty, filled_avg_price=None, submitted_at=None, filled_at=None,
    )


@pytest.mark.asyncio
async def test_walks_from_mid_toward_bid_until_filled():
    broker = AsyncMock()
    broker.submit_order.return_value = _order(OrderStatus.NEW, 0)
    executor = AsyncMock()
    executor.await_fill.side_effect = [_order(OrderStatus.CANCELED, 0), _order(OrderStatus.FILLED, 2)]

    fill = await close_with_price_walk(broker, executor, STRATEGY, 2, {CONTRACT.symbol: CONTRACT}, "AAPL")

    assert fill.filled
    prices = [call.args[0].limit_price for call in broker.submit_order.await_args_list]
    assert prices == [0.40, 0.30]  # mid, then halfway to the 0.20 bid


@pytest.mark.asyncio
async def test_ends_at_the_bid_when_nothing_fills():
    broker = AsyncMock()
    broker.submit_order.return_value = _order(OrderStatus.NEW, 0)
    executor = AsyncMock()
    executor.await_fill.return_value = _order(OrderStatus.CANCELED, 0)

    fill = await close_with_price_walk(broker, executor, STRATEGY, 2, {CONTRACT.symbol: CONTRACT}, "AAPL")

    assert not fill.filled
    assert [c.args[0].limit_price for c in broker.submit_order.await_args_list] == [0.40, 0.30, 0.20]
