"""Automatic repair of confirmed broker-vs-tracking drift (see
reconciliation_loop.py for how drift is detected).

The drift that actually cost trading time was mechanical, and so is the fix:

- tracked but not held (phantom): the ETHA/NVDL/ONDS/SOXS records sat in
  trade_management_positions for four weeks, blocked entries in those
  symbols and pushed the exposure checks to "580% of cap", so every sleeve
  stopped entering from 09-07 to 09-28. The fix is to drop the record --
  there's nothing at the broker to manage.
- held but not tracked (stray): IBIT/TSLA/PCG/BITO/SOXL/WMT/SPCX options rode
  into expiry with no stop or exit, and INTC was exercised into 800 shares.
  Nothing manages a stray, so the fix is to close it at market.

Each fix re-verifies against the broker directly right before acting (a
single-symbol lookup, not the bulk listing that flagged it), so a bad bulk
read can't trigger it. Quantity mismatches, and forex trades OANDA closed
(the forex loops' own syncs book those, with P&L), are left alone.
"""

import logging
import re
from datetime import datetime

from broker.models import OrderRequest, OrderSide, OrderType, PositionIntent, TimeInForce
from trade_management.close_confirmation import confirm_close_fill
from utils.time import is_equity_market_open

from .context import AppContext

logger = logging.getLogger(__name__)

# OCC option symbol, e.g. ETHA260925P00016500.
_OCC_SYMBOL = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


async def auto_fix(context: AppContext, book: str, kind: str, symbol: str, now: datetime) -> str | None:
    """Applies the fix for one confirmed discrepancy. Returns a one-line
    description of what was done, or None if this kind isn't auto-fixed or
    the re-check found nothing to do."""
    if book == "equities" and kind == "tracked_not_held":
        return await _untrack_phantom(context, symbol)
    if book == "equities" and kind == "held_not_tracked":
        return await _close_equities_stray(context, symbol, now)
    if book == "forex" and kind == "held_not_tracked":
        return await _close_forex_stray(context, symbol)
    return None


async def _untrack_phantom(context: AppContext, symbol: str) -> str | None:
    if await context.broker.get_position(symbol) is not None:
        return None  # it's there after all; the next run re-evaluates

    removed = []
    for repo in (context.position_repository, context.breakout_position_repository, context.sr_option_position_repository):
        for record in await repo.get_all():
            if any(leg.symbol == symbol for leg in record.legs):
                await repo.delete(record.symbol)
                removed.append(record.symbol)
    for repo in (context.stock_position_repository, context.sr_stock_position_repository):
        if await repo.get(symbol) is not None:
            await repo.delete(symbol)
            removed.append(symbol)

    if not removed:
        return None
    logger.warning("reconciliation auto-fix: untracked phantom %s (broker holds nothing)", symbol)
    return f"removed the tracking record ({', '.join(removed)}); the broker holds nothing"


async def _close_equities_stray(context: AppContext, symbol: str, now: datetime) -> str | None:
    if not is_equity_market_open(now):
        return None  # retried on the first run after the open

    from .reconciliation_loop import tracked_quantities  # circular at module load

    if symbol in await tracked_quantities(context):
        return None  # a loop started tracking it since the check
    position = await context.broker.get_position(symbol)
    if position is None or position.qty == 0:
        return None

    close_side = OrderSide.SELL if position.side is OrderSide.BUY else OrderSide.BUY
    intent = None
    if _OCC_SYMBOL.match(symbol):
        intent = PositionIntent.SELL_TO_CLOSE if close_side is OrderSide.SELL else PositionIntent.BUY_TO_CLOSE
    qty = abs(position.qty)
    order = await context.broker.submit_order(
        OrderRequest(
            symbol=symbol, qty=qty, side=close_side, order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY, position_intent=intent,
        )
    )
    fill = await confirm_close_fill(context.executor, context.broker, order.order_id, symbol)
    if not fill.filled:
        return f"tried to close the untracked {qty:g} at market; it didn't fill, will retry"
    logger.warning(
        "reconciliation auto-fix: closed untracked stray %s x%g (unrealized P&L at close ~%.2f)",
        symbol, fill.filled_qty, position.unrealized_pl,
    )
    return f"closed the untracked {fill.filled_qty:g} at market (unrealized P&L was {position.unrealized_pl:+.2f})"


async def _close_forex_stray(context: AppContext, key: str) -> str | None:
    pair, trade_id = key.split(":", 1)

    from .reconciliation_loop import tracked_forex_units  # circular at module load

    if key in await tracked_forex_units(context):
        return None
    if trade_id not in await context.forex_broker.get_open_trade_ids():
        return None
    await context.forex_broker.close_trade(trade_id)
    logger.warning("reconciliation auto-fix: closed untracked forex trade %s (%s)", trade_id, pair)
    return f"closed untracked OANDA trade {trade_id}"
