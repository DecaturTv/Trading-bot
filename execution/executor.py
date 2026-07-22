import asyncio

from broker.base import BrokerAdapter
from broker.models import MultiLegOrderRequest, Order, OrderStatus, TimeInForce
from options.models import OptionStrategy

from .models import ExecutionResult
from .order_builder import build_open_order_request

_TERMINAL_STATUSES = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}


class OrderTimeoutError(Exception):
    pass


class OrderExecutor:
    def __init__(self, broker: BrokerAdapter):
        self._broker = broker

    async def execute(
        self, strategy: OptionStrategy, qty: int, time_in_force: TimeInForce = TimeInForce.DAY
    ) -> ExecutionResult:
        request = build_open_order_request(strategy, qty, time_in_force)
        if isinstance(request, MultiLegOrderRequest):
            order = await self._broker.submit_multi_leg_order(request)
        else:
            order = await self._broker.submit_order(request)
        underlying_symbol = strategy.legs[0].contract.underlying_symbol
        return ExecutionResult(order=order, strategy_type=strategy.strategy_type, symbol=underlying_symbol)

    async def await_fill(self, order_id: str, poll_interval: float = 1.0, max_attempts: int = 30) -> Order:
        # Polling, not streaming: no websocket/event infra exists yet in this
        # project (that's dashboard/'s territory later).
        for attempt in range(max_attempts):
            order = await self._broker.get_order(order_id)
            if order.status in _TERMINAL_STATUSES:
                return order
            if attempt < max_attempts - 1:
                await asyncio.sleep(poll_interval)
        raise OrderTimeoutError(f"order {order_id} did not reach a terminal status after {max_attempts} attempts")
