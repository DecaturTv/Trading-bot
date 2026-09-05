"""Support/resistance direct-equity strategy, run in parallel with the
momentum-based stock_loop.py on its own $3,000 synthetic paper account.

This is a new, independently backtested strategy (_scratch_sr_backtest.py:
walk-forward across 2.5 months of real 5Min bars, 165 symbols, 18,221 trades,
+0.21R to +0.29R expectancy, positive in every one of the 9 weeks sampled,
holds under realistic concurrent-position caps) -- see project memory. It
does not touch the momentum model's signal, positions, or capital in any way;
it runs fully isolated so it can prove itself live before either replacing
anything or getting more capital.

Isolation, same pattern as breakout_loop.py:
  * open positions in the `sr_stock_positions` table (context.sr_stock_position_repository)
  * realized P&L under asset_class "sr_stocks" in ml_trade_outcomes
  * halt / loss-limit scope "sr_stocks"
  * equity = settings.sr_stock_account_start_balance + realized sr_stocks P&L

Long-only, same constraint as stock_loop.py: a bearish S/R signal is
sr_options_loop's long_put territory, not shorting shares.
"""

import logging
from dataclasses import replace
from datetime import datetime, timedelta

from alerts.models import Alert, Severity
from broker.models import OrderRequest, OrderSide, OrderType, TimeInForce
from decision_engine.models import TradeDirection
from decision_engine.support_resistance import BACKTESTED_SR_CONFIG, sr_signal
from ml.trade_outcomes import get_live_trade_statistics
from risk.halt_manager import evaluate_loss_limits
from risk.sizing import contracts_for_budget, position_budget_dollars
from risk.streak import current_positive_day_streak, streak_adjusted_fraction
from stocks.sr_models import OpenSRStockPositionRecord, SRStockPositionState
from trade_management.expiry import trading_days_until
from trade_management.sr_exit_rules import BACKTESTED_SR_EXIT_CONFIG, SRExitAction, evaluate_sr_exit
from utils.time import is_equity_market_open, is_us_market_weekday

from .context import AppContext, get_effective_sr_stock_account
from .stock_loop import EventCallback, _emit

logger = logging.getLogger(__name__)

_TIMEFRAME = "5Min"
# Enough calendar days to cover BACKTESTED_SR_CONFIG.min_bars_required
# (780 + 10 + 14 + 2 = 806 5Min bars) at ~78 bars/trading day, plus buffer
# for weekends/holidays.
_BARS_LOOKBACK_DAYS = 21
_ASSET_CLASS = "sr_stocks"
_HALT_SCOPE = "sr_stocks"


async def sr_stock_entry_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if not is_equity_market_open(now):
        logger.info("sr stock entry cycle skipped: market closed")
        return
    if await context.halt_manager.is_halted(_HALT_SCOPE):
        logger.info("sr stock entry cycle skipped: trading halted")
        return

    account = await get_effective_sr_stock_account(context)
    max_price = account.equity * context.pre_trade_checker.max_total_exposure_pct
    symbols = await context.universe_manager.get_active_symbols(now, max_price=max_price)
    logger.info("sr stock entry cycle: scanning %d symbols", len(symbols))
    for symbol in symbols:
        try:
            await _maybe_enter(context, symbol, now, on_event)
        except Exception:
            logger.exception("sr stock entry cycle failed for %s", symbol)


async def _maybe_enter(context: AppContext, symbol: str, now: datetime, on_event: EventCallback) -> None:
    if await context.sr_stock_position_repository.get(symbol) is not None:
        return  # already have an open S/R position in this symbol
    if await context.stock_position_repository.get(symbol) is not None:
        return  # already holding this symbol as a direct (momentum-model) stock position
    if await context.position_repository.get(symbol) is not None:
        return  # already holding this symbol via the momentum options loop

    await context.ingestion_service.ingest_incremental(symbol, _TIMEFRAME, end=now)
    bars = await context.bars_repository.get_bars(symbol, _TIMEFRAME, now - timedelta(days=_BARS_LOOKBACK_DAYS), now)
    signal = sr_signal(bars, BACKTESTED_SR_CONFIG)
    if signal is None:
        return
    # Long-only: a bearish S/R signal belongs to sr_options_loop's long_put
    # path, not shorting shares (would need margin/borrow handling this
    # doesn't have) -- same rule stock_loop.py applies to the momentum model.
    if signal.direction is not TradeDirection.BULLISH:
        return

    quote = await context.broker.get_latest_quote(symbol)
    entry_price = quote.ask_price
    if entry_price <= 0:
        return

    async with context.sr_stock_entry_lock:
        if await context.sr_stock_position_repository.get(symbol) is not None:
            return  # opened by a concurrent entry cycle while we were scanning
        if await context.stock_position_repository.get(symbol) is not None:
            return
        if await context.position_repository.get(symbol) is not None:
            return

        account = await get_effective_sr_stock_account(context)
        stats = await get_live_trade_statistics(context.trade_outcome_repository, asset_class=_ASSET_CLASS)
        kelly_result = context.sr_stock_kelly_sizer.size(stats)
        daily_pnls = await context.trade_outcome_repository.daily_pnls(asset_class=_ASSET_CLASS)
        positive_day_streak = current_positive_day_streak(daily_pnls)
        kelly_result = replace(
            kelly_result, position_fraction=streak_adjusted_fraction(kelly_result.position_fraction, positive_day_streak)
        )
        budget = position_budget_dollars(account.equity, kelly_result)
        qty = contracts_for_budget(budget, entry_price)
        if qty <= 0:
            logger.info(
                "sr stock entry cycle skipped %s: budget $%.2f can't afford one share at ask $%.2f",
                symbol, budget, entry_price,
            )
            return

        positions = await context.broker.get_positions()
        estimated_cost = qty * entry_price
        check = await context.pre_trade_checker.evaluate(account, positions, symbol, estimated_cost)
        if not check.passed:
            failed = [f"{c.name}: {c.reason}" for c in check.checks if not c.passed]
            logger.info("sr stock entry cycle skipped %s: pre-trade check failed (%s)", symbol, "; ".join(failed))
            return

        order = await context.broker.submit_order(
            OrderRequest(
                symbol=symbol, qty=qty, side=OrderSide.BUY, order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.DAY, limit_price=entry_price,
            )
        )

        record = OpenSRStockPositionRecord(
            symbol=symbol,
            direction=signal.direction,
            entry_date=now.date(),
            state=SRStockPositionState(
                symbol=symbol, qty=qty, entry_price=entry_price, stop_price=signal.stop, target_price=signal.target,
            ),
        )
        await context.sr_stock_position_repository.upsert(record, updated_at=now)

    await context.alert_manager.send(
        Alert(
            title=f"[sr] Opened stock position on {symbol}",
            message=(
                f"qty={qty} entry={entry_price:.2f} stop={signal.stop:.2f} target={signal.target:.2f} "
                f"reward_r={signal.reward_r:.2f} order={order.order_id}"
            ),
            severity=Severity.INFO,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "sr_stock_position_opened", "symbol": symbol, "qty": qty, "entry_price": entry_price})


async def sr_stock_position_management_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if not is_equity_market_open(now):
        logger.info("sr stock position management cycle skipped: market closed")
        return

    records = await context.sr_stock_position_repository.get_all()
    logger.info("sr stock position management cycle: checking %d tracked positions", len(records))
    for record in records:
        try:
            await _manage_position(context, record, now, on_event)
        except Exception:
            logger.exception("sr stock position management failed for %s", record.symbol)


async def _manage_position(context: AppContext, record: OpenSRStockPositionRecord, now: datetime, on_event: EventCallback) -> None:
    quote = await context.broker.get_latest_quote(record.symbol)
    current_price = quote.bid_price  # what selling right now would fetch -- conservative mark
    if current_price <= 0:
        return

    decision = evaluate_sr_exit(
        direction=record.direction, stop_price=record.state.stop_price, target_price=record.state.target_price,
        current_price=current_price, trading_days_held=trading_days_until(now.date(), record.entry_date),
        stop_streak=record.state.stop_streak, config=BACKTESTED_SR_EXIT_CONFIG,
    )
    if decision.action is SRExitAction.NONE:
        if decision.stop_streak != record.state.stop_streak:
            updated_state = replace(record.state, stop_streak=decision.stop_streak)
            await context.sr_stock_position_repository.upsert(replace(record, state=updated_state), updated_at=now)
        return

    order = await context.broker.submit_order(
        OrderRequest(
            symbol=record.symbol, qty=record.state.qty, side=OrderSide.SELL, order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY, limit_price=current_price,
        )
    )

    pnl = (current_price - record.state.entry_price) * record.state.qty
    await context.trade_outcome_repository.record_outcome(record.symbol, now, pnl, asset_class=_ASSET_CLASS)
    await context.sr_stock_position_repository.delete(record.symbol)

    severity = Severity.WARNING if decision.action is SRExitAction.STOP_LOSS else Severity.INFO
    await context.alert_manager.send(
        Alert(
            title=f"[sr] {decision.action.value} on {record.symbol} (stock)",
            message=f"{decision.reason} pnl={pnl:.2f} order={order.order_id}",
            severity=severity,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "sr_stock_position_closed", "symbol": record.symbol, "action": decision.action.value, "pnl": pnl})


async def sr_stock_loss_limit_check_cycle(context: AppContext, now: datetime) -> None:
    """Same policy and paper-mode notify-only behavior as
    trading_loop.loss_limit_check_cycle / breakout_loop.breakout_loss_limit_check_cycle,
    scoped to asset_class/halt scope "sr_stocks"."""
    if await context.halt_manager.is_halted(_HALT_SCOPE):
        return

    account = await get_effective_sr_stock_account(context)
    if account.equity <= 0:
        return

    day_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    daily_pnl_pct = sum(await context.trade_outcome_repository.pnls_since(day_start, asset_class=_ASSET_CLASS)) / account.equity

    if context.settings.trading_mode == "paper":
        weekly_pnl_pct = 0.0
    else:
        week_start = day_start - timedelta(days=now.weekday())
        weekly_pnl_pct = sum(
            await context.trade_outcome_repository.pnls_since(week_start, asset_class=_ASSET_CLASS)
        ) / account.equity

    if context.settings.trading_mode == "live":
        triggered = await context.halt_manager.check_and_halt_on_loss_limits(
            daily_pnl_pct, weekly_pnl_pct, context.settings.daily_loss_limit_pct, context.settings.weekly_loss_limit_pct, now,
            scope=_HALT_SCOPE,
        )
        if triggered:
            await context.alert_manager.send(
                Alert(
                    title="Trading halted (sr_stocks): loss limit breached",
                    message=f"daily_pnl_pct={daily_pnl_pct:.2%} weekly_pnl_pct={weekly_pnl_pct:.2%}",
                    severity=Severity.CRITICAL,
                    timestamp=now,
                )
            )
    else:
        breach_reason = evaluate_loss_limits(
            daily_pnl_pct, weekly_pnl_pct, context.settings.daily_loss_limit_pct, context.settings.weekly_loss_limit_pct
        )
        if breach_reason is not None:
            await context.alert_manager.send(
                Alert(
                    title="Loss limit breached (sr_stocks, paper trading — not halted)",
                    message=f"{breach_reason}; daily_pnl_pct={daily_pnl_pct:.2%} weekly_pnl_pct={weekly_pnl_pct:.2%}",
                    severity=Severity.WARNING,
                    timestamp=now,
                    dedup_key="loss-limit-breach-sr-stocks-paper",
                )
            )


async def sr_stock_progress_report_cycle(context: AppContext, now: datetime) -> None:
    if context.progress_notifier is None:
        return
    if not is_us_market_weekday(now):
        return

    account = await get_effective_sr_stock_account(context)
    positions = await context.sr_stock_position_repository.get_all()
    halted = await context.halt_manager.is_halted(_HALT_SCOPE)

    day_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    daily_pnl = sum(await context.trade_outcome_repository.pnls_since(day_start, asset_class=_ASSET_CLASS))
    cumulative_pnl = sum(await context.trade_outcome_repository.recent_pnls(asset_class=_ASSET_CLASS))
    closed_today = [
        trade
        for trade in await context.trade_outcome_repository.recent_trades(limit=50, asset_class=_ASSET_CLASS)
        if trade["closed_at"] >= day_start
    ]

    lines = [
        f"equity=${account.equity:,.2f} day_pnl=${daily_pnl:,.2f} cumulative_pnl=${cumulative_pnl:,.2f} "
        f"open_positions={len(positions)} status={'HALTED' if halted else 'running'}"
    ]
    if positions:
        lines.append("\nOpen positions:")
        lines.extend(
            f"- {p.symbol} qty={p.state.qty} entry=${p.state.entry_price:.2f} "
            f"stop=${p.state.stop_price:.2f} target=${p.state.target_price:.2f}"
            for p in positions
        )
    if closed_today:
        lines.append("\nClosed today:")
        lines.extend(f"- {trade['symbol']} pnl={trade['pnl']:+.2f}" for trade in closed_today)

    await context.progress_notifier.send(
        Alert(title="S/R stocks progress", message="\n".join(lines), severity=Severity.INFO, timestamp=now)
    )
