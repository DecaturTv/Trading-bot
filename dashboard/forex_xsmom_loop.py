"""Cross-sectional FX momentum book — the quarterly-rebalanced long/short
strategy from the 2026-09-01 research ([[trading_platform_forex_research]]).

Two cycles, both scoped to halt scope / asset_class "forex":
  * forex_xsmom_rebalance_cycle  — daily check; when a rebalance is due,
    rank every tradeable pair by trailing ~12-month daily return, close the
    old book and open the new equal-weight top-K / bottom-K book.
  * forex_xsmom_sync_cycle       — hourly; books P&L for any leg OANDA has
    closed out from under us (margin closeout, manual close).

No stop-loss / take-profit: legs live until the next rebalance. Uses the
OANDA adapter directly for daily bars (context.ingestion_service is wired to
the equities broker).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from alerts.models import Alert, Severity
from broker.models import OrderSide
from decision_engine.models import TradeDirection
from forex.conversion import quote_to_account_rate
from forex.cross_sectional import leg_units, momentum_scores, target_book
from forex.oanda_adapter import OandaError, TradeNotSettledError  # TradeNotSettledError <: OandaError
from forex.xsmom_repository import XsmomPosition
from utils.time import is_forex_market_open

from .context import AppContext, get_effective_forex_account

logger = logging.getLogger(__name__)

_ASSET_CLASS = "forex"
_HALT_SCOPE = "forex"
_DAILY_TF = "D"
_SIDE_BY_DIRECTION = {TradeDirection.BULLISH: OrderSide.BUY, TradeDirection.BEARISH: OrderSide.SELL}

EventCallback = Callable[[dict], Awaitable[None]] | None


async def _emit(on_event: EventCallback, event: dict) -> None:
    if on_event is not None:
        await on_event(event)


def _rebalance_due(last: object, now: datetime, cadence_days: int) -> bool:
    if last is None:
        return True
    return (now.date() - last).days >= cadence_days


async def _refresh_daily_bars(context: AppContext, pair: str, now: datetime, lookback_days: int) -> list[float]:
    """Top up stored daily bars for `pair` from OANDA, then return the trailing
    close series (oldest -> newest)."""
    start = now - timedelta(days=lookback_days)
    latest = await context.bars_repository.latest_timestamp(pair, _DAILY_TF)
    fetch_from = (latest + timedelta(days=1)) if latest else start
    if fetch_from < now:
        try:
            fresh = await context.forex_broker.get_bars(pair, _DAILY_TF, fetch_from, now)
            if fresh:
                await context.bars_repository.upsert_bars(_DAILY_TF, fresh)
        except Exception:
            logger.exception("xsmom: failed to refresh daily bars for %s", pair)
    bars = await context.bars_repository.get_bars(pair, _DAILY_TF, start, now)
    return [b.close for b in bars]


async def forex_xsmom_rebalance_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if context.forex_broker is None or context.forex_xsmom_repository is None:
        return
    if not context.settings.forex_xsmom_enabled:
        return
    if not is_forex_market_open(now):
        return
    if await context.halt_manager.is_halted(_HALT_SCOPE):
        return

    s = context.settings
    last = await context.forex_xsmom_repository.last_rebalance_date()
    if not _rebalance_due(last, now, s.forex_xsmom_rebalance_calendar_days):
        return

    logger.info("xsmom: rebalance due (last=%s), scoring pairs", last)
    pairs = await context.forex_broker.get_tradeable_pairs()
    lookback_days = int(s.forex_xsmom_lookback_trading_days * 1.6) + 30  # trading -> calendar, plus a buffer
    closes_by_pair: dict[str, list[float]] = {}
    for pair in pairs:
        closes = await _refresh_daily_bars(context, pair, now, lookback_days)
        if closes:
            closes_by_pair[pair] = closes

    scores = momentum_scores(closes_by_pair, s.forex_xsmom_lookback_trading_days)
    legs = target_book(scores, s.forex_xsmom_top_k)
    if not legs:
        logger.warning(
            "xsmom: only %d pairs have %d+ daily bars; need %d for a book — skipping rebalance",
            len(scores), s.forex_xsmom_lookback_trading_days, 2 * s.forex_xsmom_top_k,
        )
        return

    # --- close the whole existing book ---
    closed = 0
    for pos in await context.forex_xsmom_repository.get_all():
        try:
            await context.forex_broker.close_trade(pos.oanda_trade_id)
        except OandaError:
            logger.exception("xsmom: close of %s (%s) failed; dropping tracking anyway", pos.pair, pos.oanda_trade_id)
        await _book_outcome(context, pos, now)
        await context.forex_xsmom_repository.delete(pos.pair)
        closed += 1

    # --- open the new book ---
    account = await get_effective_forex_account(context)
    tradeable = set(pairs)
    opened = 0
    for leg in legs:
        try:
            bid, ask = await context.forex_broker.get_pricing(leg.pair)
            mid = (bid + ask) / 2
            rate = await quote_to_account_rate(context.forex_broker, leg.pair, account.currency, tradeable)
            if rate is None or mid <= 0:
                logger.warning("xsmom: no price/rate for %s, skipping leg", leg.pair)
                continue
            units = leg_units(account.equity, s.forex_xsmom_gross_leverage, leg, mid, rate)
            if units <= 0:
                logger.info("xsmom: leg %s sizes to 0 units on $%.0f equity, skipping", leg.pair, account.equity)
                continue
            side = _SIDE_BY_DIRECTION[leg.direction]
            trade_id = await context.forex_broker.submit_market_order(leg.pair, units, side)
            await context.forex_xsmom_repository.upsert(
                XsmomPosition(
                    pair=leg.pair, direction=leg.direction, units=units,
                    entry_price=mid, score=leg.score, oanda_trade_id=trade_id, opened_at=now,
                )
            )
            opened += 1
        except Exception:
            logger.exception("xsmom: failed to open leg %s", leg.pair)

    await context.forex_xsmom_repository.set_last_rebalance_date(now.date())
    book = ", ".join(f"{'+' if leg.direction is TradeDirection.BULLISH else '-'}{leg.pair}" for leg in legs)
    await context.alert_manager.send(
        Alert(
            title=f"[forex-xsmom] Rebalanced: {opened} legs opened, {closed} closed",
            message=f"12m-momentum book (equity ${account.equity:,.0f}): {book}",
            severity=Severity.INFO,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "forex_xsmom_rebalanced", "opened": opened, "closed": closed})


async def forex_xsmom_sync_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    """Book P&L for any leg OANDA has closed out from under us between
    rebalances (margin closeout, manual close)."""
    if context.forex_broker is None or context.forex_xsmom_repository is None:
        return
    if not context.settings.forex_xsmom_enabled:
        return

    tracked = await context.forex_xsmom_repository.get_all()
    if not tracked:
        return
    open_ids = await context.forex_broker.get_open_trade_ids()
    for pos in tracked:
        if pos.oanda_trade_id in open_ids:
            continue
        await _book_outcome(context, pos, now)
        await context.forex_xsmom_repository.delete(pos.pair)
        await context.alert_manager.send(
            Alert(
                title=f"[forex-xsmom] {pos.pair} closed outside rebalance",
                message=f"trade {pos.oanda_trade_id} no longer open; P&L booked",
                severity=Severity.WARNING,
                timestamp=now,
            )
        )
        await _emit(on_event, {"type": "forex_xsmom_leg_closed", "pair": pos.pair})


async def _book_outcome(context: AppContext, pos: XsmomPosition, now: datetime) -> None:
    try:
        pnl = await context.forex_broker.get_trade_realized_pnl(pos.oanda_trade_id)
    except Exception:
        logger.exception("xsmom: could not read realized P&L for %s (%s); booking 0", pos.pair, pos.oanda_trade_id)
        pnl = 0.0
    await context.trade_outcome_repository.record_outcome(
        pos.pair, now, pnl, asset_class=_ASSET_CLASS,
        details={"strategy": "xsmom", "direction": pos.direction.value, "units": pos.units, "score": pos.score},
    )
