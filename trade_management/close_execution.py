"""Close an option position without dumping it at the bid first.

Closes used to go out as a limit at the raw bid. On a thin book that is
wherever the worst market maker is quoting: HPQ calls bought at 0.95 were
sold at 0.02 two minutes later, and the contract never traded below 0.35
over the next four days (see project memory on the wick-out analysis).

close_with_price_walk tries the mid first, then halfway to the bid, then the
bid itself, cancelling each unfilled step before the next (confirm_close_fill
already cancels on timeout). Worst case it ends where it used to start, about
20 seconds later.
"""

import logging

from broker.base import BrokerAdapter
from broker.models import MultiLegOrderRequest, OptionContract
from execution.executor import OrderExecutor
from options.models import OptionStrategy

from .close_confirmation import CloseFillResult, confirm_close_fill
from .close_order_builder import build_close_order_request

logger = logging.getLogger(__name__)

# (price_step, seconds to wait for a fill); the last step is the bid with
# the normal fill wait.
CLOSE_PRICE_WALK = ((0.0, 10), (0.5, 10), (1.0, 30))


async def close_with_price_walk(
    broker: BrokerAdapter,
    executor: OrderExecutor,
    strategy: OptionStrategy,
    qty: int,
    current_contracts: dict[str, OptionContract],
    symbol: str,
) -> CloseFillResult:
    steps = CLOSE_PRICE_WALK if len(strategy.legs) == 1 else ((1.0, 30),)
    fill = CloseFillResult(filled=False, filled_qty=0.0, order=None)
    for price_step, wait_attempts in steps:
        request = build_close_order_request(strategy, qty, current_contracts, price_step=price_step)
        if isinstance(request, MultiLegOrderRequest):
            order = await broker.submit_multi_leg_order(request)
        else:
            order = await broker.submit_order(request)
        fill = await confirm_close_fill(executor, broker, order.order_id, symbol, max_attempts=wait_attempts)
        if fill.filled:
            # A partial fill stops the walk; the rest stays tracked and the
            # next management cycle closes it.
            return fill
        logger.info("close of %s at price step %.1f didn't fill; stepping toward the bid", symbol, price_step)
    return fill
