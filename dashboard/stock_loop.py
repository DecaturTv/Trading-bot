import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

from alerts.models import Alert, Severity
from broker.models import OrderRequest, OrderSide, OrderType, TimeInForce
from decision_engine.models import TradeDirection
from ml.trade_outcomes import get_live_trade_statistics
from risk.sizing import contracts_for_budget, position_budget_dollars
from scanner.scans import scan_gap, scan_momentum, scan_unusual_volume
from stocks.models import OpenStockPositionRecord
from trade_management.exit_rules import evaluate_exit
from trade_management.models import ExitAction, PositionState
from utils.time import is_equity_market_open

from .context import AppContext, get_effective_account

logger = logging.getLogger(__name__)

_BARS_LOOKBACK_DAYS = 90
_MIN_BARS_FOR_SIGNAL = 30
_SCAN_FUNCTIONS = (scan_unusual_volume, scan_gap, scan_momentum)
_NEVER_EXPIRES = 10**9  # shares have no expiration; keeps evaluate_exit's EXPIRY_EXIT branch from ever firing

EventCallback = Callable[[dict], Awaitable[None]] | None


async def _emit(on_event: EventCallback, event: dict) -> None:
    if on_event is not None:
        await on_event(event)


async def stock_entry_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    """Direct long-only equity entries — a second, independent vehicle
    alongside the options entry cycle, scanning the same universe/signal.
    Market-hours-gated and halt-gated up front, same as the options cycle."""
    if not is_equity_market_open(now):
        logger.info("stock entry cycle skipped: market closed")
        return
    if await context.halt_manager.is_halted("equities"):
        logger.info("stock entry cycle skipped: trading halted")
        return

    symbols = await context.universe_manager.get_universe(now)
    logger.info("stock entry cycle: scanning %d symbols", len(symbols))
    for symbol in symbols:
        try:
            await _maybe_enter_stock(context, symbol, now, on_event)
        except Exception:
            logger.exception("stock entry cycle failed for %s", symbol)


async def _maybe_enter_stock(context: AppContext, symbol: str, now: datetime, on_event: EventCallback) -> None:
    if await context.position_repository.get(symbol) is not None:
        return  # already holding an options position in this symbol
    if await context.stock_position_repository.get(symbol) is not None:
        return  # already holding a stock position in this symbol

    await context.ingestion_service.ingest_incremental(symbol, "1Day", end=now)
    bars = await context.bars_repository.get_bars(symbol, "1Day", now - timedelta(days=_BARS_LOOKBACK_DAYS), now)
    if len(bars) < _MIN_BARS_FOR_SIGNAL:
        return

    scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, bars)) is not None]
    signal = context.decision_model.score(symbol, bars, scan_hits, context.settings.confidence_threshold)
    # Long-only: a bearish signal is the options long_put path's territory,
    # not shorting shares (would need margin/borrow handling this doesn't have).
    if not signal.meets_threshold or signal.direction is not TradeDirection.BULLISH:
        return

    quote = await context.broker.get_latest_quote(symbol)
    entry_price = quote.ask_price
    if entry_price <= 0:
        return

    # See dashboard.trading_loop._maybe_enter for why this section is locked:
    # this cycle and the option 5m/15m/1h/1d cycles all commit against the
    # same Alpaca account/budget as independent concurrent asyncio tasks.
    async with context.equities_entry_lock:
        if await context.position_repository.get(symbol) is not None:
            return  # opened by a concurrent entry cycle while we were scanning
        if await context.stock_position_repository.get(symbol) is not None:
            return

        account = await get_effective_account(context)
        positions = await context.broker.get_positions()
        check = await context.pre_trade_checker.evaluate(account, positions, symbol, entry_price)
        if not check.passed:
            return

        stats = await get_live_trade_statistics(context.trade_outcome_repository, asset_class="equities")
        kelly_result = context.kelly_sizer.size(stats)
        budget = position_budget_dollars(account.equity, kelly_result)
        qty = contracts_for_budget(budget, entry_price)
        if qty <= 0:
            return

        order = await context.broker.submit_order(
            OrderRequest(
                symbol=symbol, qty=qty, side=OrderSide.BUY, order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.DAY, limit_price=entry_price,
            )
        )

        record = OpenStockPositionRecord(
            symbol=symbol,
            direction=signal.direction,
            entry_date=now.date(),
            state=PositionState(symbol=symbol, qty=qty, entry_cost_per_unit=entry_price, scaled_out=False, peak_gain_pct=0.0),
        )
        await context.stock_position_repository.upsert(record, updated_at=now)

    await context.alert_manager.send(
        Alert(
            title=f"Opened stock position on {symbol}",
            message=f"qty={qty} entry_price={entry_price:.2f} confidence={signal.confidence:.1f} order={order.order_id}",
            severity=Severity.INFO,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "stock_position_opened", "symbol": symbol, "qty": qty, "entry_price": entry_price})


async def stock_position_management_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if not is_equity_market_open(now):
        logger.info("stock position management cycle skipped: market closed")
        return

    records = await context.stock_position_repository.get_all()
    logger.info("stock position management cycle: checking %d tracked positions", len(records))
    for record in records:
        try:
            await _manage_stock_position(context, record, now, on_event)
        except Exception:
            logger.exception("stock position management failed for %s", record.symbol)


async def _manage_stock_position(context: AppContext, record: OpenStockPositionRecord, now: datetime, on_event: EventCallback) -> None:
    quote = await context.broker.get_latest_quote(record.symbol)
    current_value = quote.bid_price  # what selling right now would fetch — conservative mark
    if current_value <= 0:
        return

    decision = evaluate_exit(record.state, current_value, _NEVER_EXPIRES, context.trade_management_config)
    if decision.action is ExitAction.NONE:
        return

    order = await context.broker.submit_order(
        OrderRequest(
            symbol=record.symbol, qty=decision.qty_to_close, side=OrderSide.SELL, order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY, limit_price=current_value,
        )
    )

    pnl = (current_value - record.state.entry_cost_per_unit) * decision.qty_to_close
    await context.trade_outcome_repository.record_outcome(record.symbol, now, pnl, asset_class="equities")

    remaining = record.state.qty - decision.qty_to_close
    if remaining <= 0:
        await context.stock_position_repository.delete(record.symbol)
    else:
        current_gain_pct = (current_value - record.state.entry_cost_per_unit) / record.state.entry_cost_per_unit
        peak = max(record.state.peak_gain_pct, current_gain_pct)
        updated_state = replace(record.state, qty=remaining, scaled_out=True, peak_gain_pct=peak)
        await context.stock_position_repository.upsert(replace(record, state=updated_state), updated_at=now)

    severity = Severity.WARNING if decision.action is ExitAction.STOP_LOSS else Severity.INFO
    await context.alert_manager.send(
        Alert(
            title=f"{decision.action.value} on {record.symbol} (stock)",
            message=f"{decision.reason} pnl={pnl:.2f} order={order.order_id}",
            severity=severity,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "stock_position_closed", "symbol": record.symbol, "action": decision.action.value, "pnl": pnl})
