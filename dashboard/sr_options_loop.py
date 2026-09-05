"""Support/resistance options strategy, run in parallel with the momentum
options loop (trading_loop.py) and Breakout Hunter (breakout_loop.py) on its
own $3,000 synthetic paper account.

Same new, independently backtested strategy as sr_stock_loop.py -- see that
module's docstring and project memory for the backtest this is built from.
Directional entries come from decision_engine.support_resistance.sr_signal
against the underlying's 5Min bars; calls on a bullish signal, puts on a
bearish one (same right-selection convention as breakout_loop.py). Exits are
evaluated against the *underlying's* price relative to the level-derived
stop/target (trade_management.sr_exit_rules.evaluate_sr_exit), not the
option's own premium -- the S/R thesis is about the underlying reacting to a
level, the option is just a leveraged way to express that.

Isolation, same pattern as breakout_loop.py:
  * open positions in the `sr_option_positions` table (context.sr_option_position_repository)
  * realized P&L under asset_class "sr_options" in ml_trade_outcomes
  * halt / loss-limit scope "sr_options"
  * equity = settings.sr_options_account_start_balance + realized sr_options P&L

Both options strategies (this one, Breakout Hunter, and the momentum loop)
fill through the one Alpaca paper account, so buying-power and
broker.get_positions() reflect the combined book; the "do I already hold X?"
check uses this loop's own repository plus stock_position_repository (same
"don't stack options on top of a direct stock position" rule breakout_loop.py
applies), not the other options strategies' tables -- distinct option
contracts on the same underlying don't share cost basis at the broker the way
two share positions would.
"""

import logging
from dataclasses import replace
from datetime import datetime, timedelta

from alerts.models import Alert, Severity
from broker.models import MultiLegOrderRequest, OptionRight
from decision_engine.models import TradeDirection
from decision_engine.support_resistance import BACKTESTED_SR_CONFIG, sr_signal
from ml.trade_outcomes import get_live_trade_statistics
from options.models import OptionLeg, OptionStrategy
from options.selection import select_expiration, select_strike_by_delta
from options.strategy_builders import MIN_TRADEABLE_CONTRACT_COST, build_long_call, build_long_put
from risk.halt_manager import evaluate_loss_limits
from risk.sizing import contracts_for_budget, position_budget_dollars
from risk.streak import current_positive_day_streak, streak_adjusted_fraction
from trade_management.close_order_builder import build_close_order_request
from trade_management.expiry import trading_days_until
from trade_management.models import PersistedLeg
from trade_management.pnl import current_value_per_unit as compute_current_value_per_unit
from trade_management.sr_exit_rules import BACKTESTED_SR_EXIT_CONFIG, SRExitAction, evaluate_sr_exit
from trade_management.sr_option_models import SROptionPositionRecord, SROptionPositionState
from utils.time import is_equity_market_open, is_us_market_weekday

from .context import AppContext, get_effective_sr_options_account
from .trading_loop import EventCallback, _current_contracts_for_legs, _emit

logger = logging.getLogger(__name__)

_TIMEFRAME = "5Min"
# See sr_stock_loop.py -- covers BACKTESTED_SR_CONFIG.min_bars_required
# (806 5Min bars) plus weekend/holiday buffer.
_BARS_LOOKBACK_DAYS = 21
_ASSET_CLASS = "sr_options"
_HALT_SCOPE = "sr_options"
# Reuses Breakout Hunter's already-live-tested option selection (see
# decision_engine.scoring.BREAKOUT_WEIGHTS comment) rather than introducing a
# second new set of untested option-selection parameters alongside a new
# signal source in the same change.
_TARGET_DELTA = 0.15
_TARGET_DTE = 20


async def sr_options_entry_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if not is_equity_market_open(now):
        logger.info("sr options entry cycle skipped: market closed")
        return
    if await context.halt_manager.is_halted(_HALT_SCOPE):
        logger.info("sr options entry cycle skipped: trading halted")
        return

    account = await get_effective_sr_options_account(context)
    max_price = account.equity * context.pre_trade_checker.max_total_exposure_pct
    symbols = await context.universe_manager.get_universe(now, max_price=max_price)
    logger.info("sr options entry cycle: scanning %d symbols", len(symbols))
    for symbol in symbols:
        try:
            await _maybe_enter(context, symbol, now, on_event)
        except Exception:
            logger.exception("sr options entry cycle failed for %s", symbol)


async def _maybe_enter(context: AppContext, symbol: str, now: datetime, on_event: EventCallback) -> None:
    if await context.sr_option_position_repository.get(symbol) is not None:
        return  # already have an open S/R options position in this symbol
    if await context.stock_position_repository.get(symbol) is not None:
        return  # don't stack options on top of a direct stock position

    await context.ingestion_service.ingest_incremental(symbol, _TIMEFRAME, end=now)
    bars = await context.bars_repository.get_bars(symbol, _TIMEFRAME, now - timedelta(days=_BARS_LOOKBACK_DAYS), now)
    signal = sr_signal(bars, BACKTESTED_SR_CONFIG)
    if signal is None:
        return

    right = OptionRight.CALL if signal.direction is TradeDirection.BULLISH else OptionRight.PUT
    target_date = now.date() + timedelta(days=_TARGET_DTE)
    deviation = timedelta(days=context.settings.option_max_dte_deviation_days)
    chain = await context.broker.get_option_chain(
        symbol, expiration_gte=target_date - deviation, expiration_lte=target_date + deviation
    )
    expirations = sorted({c.expiration for c in chain if c.right is right})
    if not expirations:
        logger.info("sr options entry cycle skipped %s: chain has no %s contracts near target", symbol, right.value)
        return
    expiration = select_expiration(expirations, _TARGET_DTE, now.date())

    calendar_dte = (expiration - now.date()).days
    if abs(calendar_dte - _TARGET_DTE) > context.settings.option_max_dte_deviation_days:
        logger.info(
            "sr options entry cycle skipped %s: nearest expiration %s is %d days out, too far from target %d",
            symbol, expiration, calendar_dte, _TARGET_DTE,
        )
        return

    dte = trading_days_until(expiration, now.date())
    if dte <= context.trade_management_config.min_trading_days_before_expiry:
        logger.info(
            "sr options entry cycle skipped %s: expiration %s only %d trading days out", symbol, expiration, dte,
        )
        return

    candidates = [c for c in chain if c.right is right and c.expiration == expiration]
    target_delta = _TARGET_DELTA if right is OptionRight.CALL else -_TARGET_DELTA
    try:
        contract = select_strike_by_delta(candidates, target_delta)
    except ValueError as exc:
        logger.info("sr options entry cycle skipped %s: no contract near target delta %.2f (%s)", symbol, target_delta, exc)
        return

    try:
        strategy = build_long_call(contract) if right is OptionRight.CALL else build_long_put(contract)
    except Exception:
        logger.exception("sr options: failed to build strategy for %s", symbol)
        return

    if strategy.net_debit < MIN_TRADEABLE_CONTRACT_COST:
        logger.info(
            "sr options entry cycle skipped %s: contract net_debit $%.2f below tradeable floor $%.2f",
            symbol, strategy.net_debit, MIN_TRADEABLE_CONTRACT_COST,
        )
        return

    async with context.sr_options_entry_lock:
        if await context.sr_option_position_repository.get(symbol) is not None:
            return
        if await context.stock_position_repository.get(symbol) is not None:
            return

        account = await get_effective_sr_options_account(context)
        stats = await get_live_trade_statistics(context.trade_outcome_repository, asset_class=_ASSET_CLASS)
        kelly_result = context.sr_options_kelly_sizer.size(stats)
        daily_pnls = await context.trade_outcome_repository.daily_pnls(asset_class=_ASSET_CLASS)
        positive_day_streak = current_positive_day_streak(daily_pnls)
        kelly_result = replace(
            kelly_result, position_fraction=streak_adjusted_fraction(kelly_result.position_fraction, positive_day_streak)
        )
        budget = position_budget_dollars(account.equity, kelly_result)
        qty = contracts_for_budget(budget, strategy.net_debit)
        if qty <= 0:
            logger.info(
                "sr options entry cycle skipped %s: budget $%.2f can't afford one contract at net_debit $%.2f",
                symbol, budget, strategy.net_debit,
            )
            return

        positions = await context.broker.get_positions()
        estimated_cost = qty * strategy.net_debit
        check = await context.pre_trade_checker.evaluate(account, positions, symbol, estimated_cost)
        if not check.passed:
            failed = [f"{c.name}: {c.reason}" for c in check.checks if not c.passed]
            logger.info("sr options entry cycle skipped %s: pre-trade check failed (%s)", symbol, "; ".join(failed))
            return

        result = await context.executor.execute(strategy, qty)

        leg = strategy.legs[0]
        record = SROptionPositionRecord(
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
            state=SROptionPositionState(
                symbol=symbol, qty=qty, entry_cost_per_unit=strategy.net_debit,
                stop_price=signal.stop, target_price=signal.target,
            ),
        )
        await context.sr_option_position_repository.upsert(record, updated_at=now)

    await context.alert_manager.send(
        Alert(
            title=f"[sr] Opened {strategy.strategy_type.value} on {symbol}",
            message=(
                f"qty={qty} entry_cost={strategy.net_debit:.2f} underlying_stop={signal.stop:.2f} "
                f"underlying_target={signal.target:.2f} reward_r={signal.reward_r:.2f} order={result.order.order_id}"
            ),
            severity=Severity.INFO,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "sr_options_position_opened", "symbol": symbol, "qty": qty, "entry_cost": strategy.net_debit})


async def sr_options_position_management_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if not is_equity_market_open(now):
        logger.info("sr options position management cycle skipped: market closed")
        return

    records = await context.sr_option_position_repository.get_all()
    logger.info("sr options position management cycle: checking %d tracked positions", len(records))
    for record in records:
        try:
            await _manage_position(context, record, now, on_event)
        except Exception:
            logger.exception("sr options position management failed for %s", record.symbol)


async def _manage_position(context: AppContext, record: SROptionPositionRecord, now: datetime, on_event: EventCallback) -> None:
    quote = await context.broker.get_latest_quote(record.symbol)
    current_underlying_price = (quote.bid_price + quote.ask_price) / 2
    if current_underlying_price <= 0:
        return

    decision = evaluate_sr_exit(
        direction=record.direction, stop_price=record.state.stop_price, target_price=record.state.target_price,
        current_price=current_underlying_price, trading_days_held=trading_days_until(now.date(), record.entry_date),
        stop_streak=record.state.stop_streak, config=BACKTESTED_SR_EXIT_CONFIG,
    )
    if decision.action is SRExitAction.NONE:
        if decision.stop_streak != record.state.stop_streak:
            updated_state = replace(record.state, stop_streak=decision.stop_streak)
            await context.sr_option_position_repository.upsert(replace(record, state=updated_state), updated_at=now)
        return

    current_contracts = await _current_contracts_for_legs(context, record)
    if len(current_contracts) < len(record.legs):
        logger.warning("sr options: missing current quotes for some legs of %s, skipping this cycle", record.symbol)
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

    close_request = build_close_order_request(strategy, record.state.qty, current_contracts)
    if isinstance(close_request, MultiLegOrderRequest):
        await context.broker.submit_multi_leg_order(close_request)
    else:
        await context.broker.submit_order(close_request)

    pnl = (current_value - record.state.entry_cost_per_unit) * record.state.qty
    await context.trade_outcome_repository.record_outcome(record.symbol, now, pnl, asset_class=_ASSET_CLASS)
    await context.sr_option_position_repository.delete(record.symbol)

    severity = Severity.WARNING if decision.action is SRExitAction.STOP_LOSS else Severity.INFO
    await context.alert_manager.send(
        Alert(
            title=f"[sr] {decision.action.value} on {record.symbol}",
            message=f"{decision.reason} pnl={pnl:.2f}",
            severity=severity,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "sr_options_position_closed", "symbol": record.symbol, "action": decision.action.value, "pnl": pnl})


async def sr_options_loss_limit_check_cycle(context: AppContext, now: datetime) -> None:
    if await context.halt_manager.is_halted(_HALT_SCOPE):
        return

    account = await get_effective_sr_options_account(context)
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
                    title="Trading halted (sr_options): loss limit breached",
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
                    title="Loss limit breached (sr_options, paper trading — not halted)",
                    message=f"{breach_reason}; daily_pnl_pct={daily_pnl_pct:.2%} weekly_pnl_pct={weekly_pnl_pct:.2%}",
                    severity=Severity.WARNING,
                    timestamp=now,
                    dedup_key="loss-limit-breach-sr-options-paper",
                )
            )


async def sr_options_progress_report_cycle(context: AppContext, now: datetime) -> None:
    if context.progress_notifier is None:
        return
    if not is_us_market_weekday(now):
        return

    account = await get_effective_sr_options_account(context)
    positions = await context.sr_option_position_repository.get_all()
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
            f"- {p.symbol} ({p.strategy_type.value}) qty={p.state.qty} entry=${p.state.entry_cost_per_unit:.2f}"
            for p in positions
        )
    if closed_today:
        lines.append("\nClosed today:")
        lines.extend(f"- {trade['symbol']} pnl={trade['pnl']:+.2f}" for trade in closed_today)

    await context.progress_notifier.send(
        Alert(title="S/R options progress", message="\n".join(lines), severity=Severity.INFO, timestamp=now)
    )
