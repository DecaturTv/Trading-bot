"""Shared post-submit fill verification for position entries -- the open-side
counterpart to trade_management/close_confirmation.py.

Every entry path used to submit its opening order and then immediately
persist a tracked position sized at the *requested* qty, with no check that
the order ever filled. A DAY limit order that never became marketable simply
expired at the broker, but the bot went on tracking a position that didn't
exist: ETHA/SOXS (2026-09-01) and NVDL/ONDS (2026-09-02) all ended EXPIRED at
Alpaca yet sat in trade_management_positions for four weeks -- blocking new
entries in those symbols, skewing exposure checks, and failing management
every cycle once the contracts expired. See project memory on the phantom
entry positions.

confirm_open_fill() polls the just-submitted order to a terminal status and,
if it never gets there, cancels it and re-reads it -- a partial (or full)
fill can land before the cancel does, and that quantity is real and must be
tracked or it rides unmanaged (the INTC failure mode, from the other side).
Callers must persist a position only when OpenFillResult.filled is True, and
size it off filled_qty, not the requested qty.
"""

import logging
from dataclasses import dataclass

from broker.base import BrokerAdapter
from broker.models import Order

from .executor import OrderExecutor, OrderTimeoutError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenFillResult:
    filled: bool  # True if ANY quantity filled (full or partial)
    filled_qty: float  # actual filled quantity; 0.0 if none
    order: Order | None  # final order snapshot; None if it couldn't be read


async def confirm_open_fill(
    executor: OrderExecutor, broker: BrokerAdapter, order_id: str, symbol: str
) -> OpenFillResult:
    try:
        order = await executor.await_fill(order_id)
    except OrderTimeoutError:
        logger.warning(
            "entry order %s for %s did not reach a terminal status in time; cancelling and re-checking for a "
            "partial fill",
            order_id, symbol,
        )
        try:
            await broker.cancel_order(order_id)
        except Exception:
            logger.exception("failed to cancel stuck entry order %s for %s", order_id, symbol)
        try:
            order = await broker.get_order(order_id)
        except Exception:
            logger.exception(
                "could not re-read entry order %s for %s after cancel; not tracking it -- check the broker for "
                "an untracked position",
                order_id, symbol,
            )
            return OpenFillResult(filled=False, filled_qty=0.0, order=None)

    # filled_qty, not status: a CANCELED/PENDING_CANCEL order can still carry
    # a partial fill, and that part is a real position.
    if order.filled_qty <= 0:
        logger.info(
            "entry order %s for %s ended %s with nothing filled; not tracking a position",
            order_id, symbol, order.status.value,
        )
        return OpenFillResult(filled=False, filled_qty=0.0, order=order)

    if order.filled_qty < order.qty:
        logger.warning(
            "entry order %s for %s only partially filled (%s of %s); tracking the filled qty",
            order_id, symbol, order.filled_qty, order.qty,
        )

    return OpenFillResult(filled=True, filled_qty=order.filled_qty, order=order)
