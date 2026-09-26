"""Shared post-submit fill verification for position closes.

Every position-management loop below submits a closing order and then has
to decide whether the close actually happened before it touches
trade_outcomes / position-tracking state. Every one of them used to skip
that check: submit the order, then unconditionally record a P&L and drop
the position from tracking as if the fill were guaranteed. An order that's
accepted but never fills — a DAY limit order that never becomes marketable,
a briefly-halted symbol, a thin option book — was treated as a successful
close: a trade outcome got recorded for a trade that never happened, and
the *actually still-open* broker position was deleted from tracking, so
nothing ever managed it again.

That's exactly what happened to the INTC breakout call on 2026-08-31: the
close order was submitted, a -$248 "close" was recorded, and the position
was deleted from breakout_positions — but the sell never filled. The 8
contracts sat completely untracked for 18 days and were auto-exercised into
800 shares at expiration. See project memory on the INTC trade for the full
trace.

confirm_close_fill() polls the just-submitted order to a terminal status
(reusing execution.executor.OrderExecutor.await_fill, already written for
this exact purpose on the open side but never wired up) and cancels it if
it never gets there, so a stuck order can't pile up duplicate closes on the
next management cycle. Callers must not record an outcome or drop/shrink
position tracking until CloseFillResult.filled is True, and must size any
P&L / remaining-qty bookkeeping off filled_qty, not the originally
requested qty_to_close — a partial fill on a multi-contract close is normal,
not exceptional.
"""

import logging
from dataclasses import dataclass

from broker.base import BrokerAdapter
from broker.models import Order, OrderStatus
from execution.executor import OrderExecutor, OrderTimeoutError

logger = logging.getLogger(__name__)

_FILLED_STATUSES = {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}


@dataclass(frozen=True)
class CloseFillResult:
    filled: bool  # True if ANY quantity filled (full or partial)
    filled_qty: float  # actual filled quantity; 0.0 if none
    order: Order | None  # terminal order snapshot; None on timeout


async def confirm_close_fill(
    executor: OrderExecutor, broker: BrokerAdapter, order_id: str, symbol: str
) -> CloseFillResult:
    try:
        order = await executor.await_fill(order_id)
    except OrderTimeoutError:
        logger.warning(
            "close order %s for %s did not reach a terminal status in time; cancelling so it can't pile up "
            "alongside next cycle's retry, position stays tracked",
            order_id, symbol,
        )
        try:
            await broker.cancel_order(order_id)
        except Exception:
            logger.exception("failed to cancel stuck close order %s for %s", order_id, symbol)
        return CloseFillResult(filled=False, filled_qty=0.0, order=None)

    if order.status not in _FILLED_STATUSES or order.filled_qty <= 0:
        logger.warning(
            "close order %s for %s ended %s (filled_qty=%s) instead of filled; position stays tracked, "
            "will retry next cycle",
            order_id, symbol, order.status.value, order.filled_qty,
        )
        return CloseFillResult(filled=False, filled_qty=0.0, order=order)

    if order.status is OrderStatus.PARTIALLY_FILLED:
        logger.warning(
            "close order %s for %s only partially filled (%s); position stays tracked for the remainder",
            order_id, symbol, order.filled_qty,
        )

    return CloseFillResult(filled=True, filled_qty=order.filled_qty, order=order)
