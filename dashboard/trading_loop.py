import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

from alerts.models import Alert, Severity
from broker.models import MultiLegOrderRequest, OptionRight
from decision_engine.models import TradeDirection
from ml.trade_outcomes import get_live_trade_statistics
from options.models import OptionLeg, OptionStrategy
from options.selection import select_expiration, select_strike_by_delta
from options.strategy_builders import build_long_call, build_long_put
from risk.sizing import contracts_for_budget, position_budget_dollars
from scanner.scans import scan_gap, scan_momentum, scan_unusual_volume
from trade_management.close_order_builder import build_close_order_request
from trade_management.exit_rules import evaluate_exit
from trade_management.expiry import trading_days_until
from trade_management.models import ExitAction, OpenPositionRecord, PersistedLeg, PositionState
from trade_management.pnl import current_value_per_unit as compute_current_value_per_unit
from utils.time import is_equity_market_open

from .context import AppContext, get_effective_account

logger = logging.getLogger(__name__)

_BARS_LOOKBACK_DAYS = 90
_MIN_BARS_FOR_SIGNAL = 30
_SCAN_FUNCTIONS = (scan_unusual_volume, scan_gap, scan_momentum)

# Calendar-day lookback per entry-cycle timeframe: enough bars for the
# decision engine's factor warmup (~30 bars, see _MIN_BARS_FOR_SIGNAL) plus
# buffer, scaled to each timeframe's bar density rather than reusing the
# daily figure everywhere.
_LOOKBACK_DAYS_BY_TIMEFRAME = {
    "1Day": _BARS_LOOKBACK_DAYS,
    "1Hour": 20,
    "15Min": 10,
    "5Min": 5,
}

EventCallback = Callable[[dict], Awaitable[None]] | None


async def _emit(on_event: EventCallback, event: dict) -> None:
    if on_event is not None:
        await on_event(event)


async def entry_cycle(context: AppContext, now: datetime, on_event: EventCallback = None, timeframe: str = "1Day") -> None:
    """Scans the universe for new entries on the given bar timeframe.
    Market-hours-gated and halt-gated up front so a closed market or an
    active halt skips the whole cycle without touching the broker. Runs as
    a separate scheduled cycle per timeframe (see scheduler.py) rather than
    picking one — a symbol already holding a position (tracked per-symbol,
    not per-timeframe) is skipped regardless of which timeframe would
    otherwise signal on it."""
    if not is_equity_market_open(now):
        logger.info("entry cycle (%s) skipped: market closed", timeframe)
        return
    if await context.halt_manager.is_halted("equities"):
        logger.info("entry cycle (%s) skipped: trading halted", timeframe)
        return

    symbols = await context.universe_manager.get_universe(now)
    logger.info("entry cycle (%s): scanning %d symbols", timeframe, len(symbols))
    for symbol in symbols:
        try:
            await _maybe_enter(context, symbol, now, on_event, timeframe)
        except Exception:
            logger.exception("entry cycle (%s) failed for %s", timeframe, symbol)


async def _maybe_enter(context: AppContext, symbol: str, now: datetime, on_event: EventCallback, timeframe: str = "1Day") -> None:
    if await context.position_repository.get(symbol) is not None:
        return  # already have an open position in this symbol

    lookback_days = _LOOKBACK_DAYS_BY_TIMEFRAME.get(timeframe, _BARS_LOOKBACK_DAYS)
    await context.ingestion_service.ingest_incremental(symbol, timeframe, end=now)
    bars = await context.bars_repository.get_bars(symbol, timeframe, now - timedelta(days=lookback_days), now)
    if len(bars) < _MIN_BARS_FOR_SIGNAL:
        return

    scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, bars)) is not None]
    signal = context.decision_model.score(symbol, bars, scan_hits, context.settings.confidence_threshold)
    if not signal.meets_threshold or signal.direction is TradeDirection.NEUTRAL:
        return

    right = OptionRight.CALL if signal.direction is TradeDirection.BULLISH else OptionRight.PUT
    chain = await context.broker.get_option_chain(symbol)
    expirations = sorted({c.expiration for c in chain if c.right is right})
    if not expirations:
        return
    expiration = select_expiration(expirations, context.settings.option_target_dte, now.date())

    # select_expiration picks the closest available expiration to the target
    # regardless of how close that is -- if the chain has nothing near
    # option_target_dte, it can hand back something already at or past the
    # force-close line, which would open and immediately EXPIRY_EXIT on the
    # next position-management pass. Skip the entry instead of doing that
    # round trip for nothing.
    dte = trading_days_until(expiration, now.date())
    if dte <= context.trade_management_config.min_trading_days_before_expiry:
        logger.info(
            "entry cycle (%s) skipped %s: nearest expiration %s is only %d trading days out (<= %d minimum)",
            timeframe, symbol, expiration, dte, context.trade_management_config.min_trading_days_before_expiry,
        )
        return

    candidates = [c for c in chain if c.right is right and c.expiration == expiration]

    target_delta = context.settings.option_target_delta if right is OptionRight.CALL else -context.settings.option_target_delta
    try:
        contract = select_strike_by_delta(candidates, target_delta)
    except ValueError:
        return

    try:
        strategy = build_long_call(contract) if right is OptionRight.CALL else build_long_put(contract)
    except Exception:
        logger.exception("failed to build strategy for %s", symbol)
        return

    account = await get_effective_account(context)
    positions = await context.broker.get_positions()
    check = await context.pre_trade_checker.evaluate(account, positions, symbol, strategy.net_debit)
    if not check.passed:
        return

    stats = await get_live_trade_statistics(context.trade_outcome_repository, asset_class="equities")
    kelly_result = context.kelly_sizer.size(stats)
    budget = position_budget_dollars(account.equity, kelly_result)
    qty = contracts_for_budget(budget, strategy.net_debit)
    if qty <= 0:
        return

    result = await context.executor.execute(strategy, qty)

    leg = strategy.legs[0]
    record = OpenPositionRecord(
        symbol=symbol,
        strategy_type=strategy.strategy_type,
        direction=signal.direction,
        entry_date=now.date(),
        legs=[
            PersistedLeg(
                symbol=leg.contract.symbol, strike=leg.contract.strike, expiration=leg.contract.expiration,
                right=leg.contract.right, side=leg.side,
            )
        ],
        state=PositionState(symbol=symbol, qty=qty, entry_cost_per_unit=strategy.net_debit, scaled_out=False, peak_gain_pct=0.0),
    )
    await context.position_repository.upsert(record, updated_at=now)

    await context.alert_manager.send(
        Alert(
            title=f"Opened {strategy.strategy_type.value} on {symbol}",
            message=(
                f"qty={qty} entry_cost={strategy.net_debit:.2f} confidence={signal.confidence:.1f} "
                f"timeframe={timeframe} order={result.order.order_id}"
            ),
            severity=Severity.INFO,
            timestamp=now,
        )
    )
    await _emit(
        on_event, {"type": "position_opened", "symbol": symbol, "qty": qty, "entry_cost": strategy.net_debit, "timeframe": timeframe}
    )


async def position_management_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if not is_equity_market_open(now):
        logger.info("position management cycle skipped: market closed")
        return

    records = await context.position_repository.get_all()
    logger.info("position management cycle: checking %d tracked positions", len(records))
    for record in records:
        try:
            await _manage_position(context, record, now, on_event)
        except Exception:
            logger.exception("position management failed for %s", record.symbol)


async def _manage_position(context: AppContext, record: OpenPositionRecord, now: datetime, on_event: EventCallback) -> None:
    current_contracts = await _current_contracts_for_legs(context, record)
    if len(current_contracts) < len(record.legs):
        logger.warning("missing current quotes for some legs of %s, skipping this cycle", record.symbol)
        return

    strategy = OptionStrategy(
        strategy_type=record.strategy_type,
        legs=[OptionLeg(contract=current_contracts[leg.symbol], side=leg.side) for leg in record.legs],
        net_debit=record.state.entry_cost_per_unit,
        max_loss=record.state.entry_cost_per_unit,
        max_gain=None,
        net_delta=0.0,
    )

    current_value = compute_current_value_per_unit(strategy, current_contracts)
    nearest_expiration = min(leg.expiration for leg in record.legs)
    dte = trading_days_until(nearest_expiration, now.date())

    decision = evaluate_exit(record.state, current_value, dte, context.trade_management_config)
    if decision.action is ExitAction.NONE:
        return

    close_request = build_close_order_request(strategy, decision.qty_to_close, current_contracts)
    if isinstance(close_request, MultiLegOrderRequest):
        await context.broker.submit_multi_leg_order(close_request)
    else:
        await context.broker.submit_order(close_request)

    pnl = (current_value - record.state.entry_cost_per_unit) * decision.qty_to_close
    await context.trade_outcome_repository.record_outcome(record.symbol, now, pnl, asset_class="equities")

    remaining = record.state.qty - decision.qty_to_close
    if remaining <= 0:
        await context.position_repository.delete(record.symbol)
    else:
        current_gain_pct = (current_value - record.state.entry_cost_per_unit) / record.state.entry_cost_per_unit
        peak = max(record.state.peak_gain_pct, current_gain_pct)
        updated_state = replace(record.state, qty=remaining, scaled_out=True, peak_gain_pct=peak)
        await context.position_repository.upsert(replace(record, state=updated_state), updated_at=now)

    severity = Severity.WARNING if decision.action is ExitAction.STOP_LOSS else Severity.INFO
    await context.alert_manager.send(
        Alert(title=f"{decision.action.value} on {record.symbol}", message=f"{decision.reason} pnl={pnl:.2f}", severity=severity, timestamp=now)
    )
    await _emit(on_event, {"type": "position_closed", "symbol": record.symbol, "action": decision.action.value, "pnl": pnl})


async def _current_contracts_for_legs(context: AppContext, record: OpenPositionRecord) -> dict:
    contracts = {}
    expirations = {leg.expiration for leg in record.legs}
    for expiration in expirations:
        chain = await context.broker.get_option_chain(record.symbol, expiration_gte=expiration, expiration_lte=expiration)
        for contract in chain:
            contracts[contract.symbol] = contract
    return {leg.symbol: contracts[leg.symbol] for leg in record.legs if leg.symbol in contracts}


async def loss_limit_check_cycle(context: AppContext, now: datetime) -> None:
    """Daily/weekly loss limits as a circuit breaker, independent of any
    single trade's sizing — see project memory. Uses current account equity
    as the denominator rather than a start-of-day snapshot (not yet tracked
    anywhere), which slightly understates loss % since it already reflects
    the day's losses; a reasonable approximation, not exact.
    """
    if await context.halt_manager.is_halted("equities"):
        return

    account = await get_effective_account(context)
    if account.equity <= 0:
        return

    day_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    week_start = day_start - timedelta(days=now.weekday())

    daily_pnl_pct = sum(await context.trade_outcome_repository.pnls_since(day_start, asset_class="equities")) / account.equity
    weekly_pnl_pct = sum(await context.trade_outcome_repository.pnls_since(week_start, asset_class="equities")) / account.equity

    triggered = await context.halt_manager.check_and_halt_on_loss_limits(
        daily_pnl_pct, weekly_pnl_pct, context.settings.daily_loss_limit_pct, context.settings.weekly_loss_limit_pct, now,
        scope="equities",
    )
    if triggered:
        await context.alert_manager.send(
            Alert(
                title="Trading halted (equities): loss limit breached",
                message=f"daily_pnl_pct={daily_pnl_pct:.2%} weekly_pnl_pct={weekly_pnl_pct:.2%}",
                severity=Severity.CRITICAL,
                timestamp=now,
            )
        )


async def progress_report_cycle(context: AppContext, now: datetime) -> None:
    """Periodic Discord status ping for the equities/options side — separate
    from the severity-gated AlertManager channels since this is a routine
    update, not an event alert. No-ops if Discord isn't configured or the
    market is closed. See forex_progress_report_cycle for the forex
    counterpart, sent as its own alert."""
    if context.progress_notifier is None:
        return
    if not is_equity_market_open(now):
        return

    account = await get_effective_account(context)
    positions = await context.position_repository.get_all()
    stock_positions = await context.stock_position_repository.get_all()
    halted = await context.halt_manager.is_halted("equities")

    day_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    daily_pnl = sum(await context.trade_outcome_repository.pnls_since(day_start, asset_class="equities"))

    message = (
        f"equity=${account.equity:,.2f} day_pnl=${daily_pnl:,.2f} "
        f"open_options_positions={len(positions)} open_stock_positions={len(stock_positions)} "
        f"status={'HALTED' if halted else 'running'}"
    )
    await context.progress_notifier.send(
        Alert(title="Stocks progress", message=message, severity=Severity.INFO, timestamp=now)
    )
