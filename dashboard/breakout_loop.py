"""The "Breakout Hunter" options strategy, run in parallel with the
momentum-only loop in trading_loop.py on its own $5,000 synthetic paper
account.

This is a deliberate near-duplicate of trading_loop.py's four cycles, hard-
wired to Breakout Hunter's config (see decision_engine.scoring.BREAKOUT_WEIGHTS
and the module constants below). It was copied rather than parametrized so a
change to the live momentum loop can't silently change this experiment — and
vice versa. If you fix a real bug in one loop, mirror it here.

Isolation:
  * signal confirmation keyed on vehicle "options_breakout"
  * open positions in the `breakout_positions` table (context.breakout_position_repository)
  * realized P&L under asset_class "breakout" in ml_trade_outcomes
  * halt / loss-limit scope "breakout"
  * equity = settings.breakout_account_start_balance + realized breakout P&L

Both strategies still fill through the one Alpaca paper account, so buying-power
and broker.get_positions() reflect the combined book; the "do I already hold X?"
check uses context.breakout_position_repository, not the broker, so the two can
independently hold the same underlying.
"""

import logging
from dataclasses import replace
from datetime import datetime, timedelta

from alerts.models import Alert, Severity
from broker.models import OptionRight
from decision_engine.confirmation import is_confirmed, update_streak
from decision_engine.entry_timing import underlying_stop_level
from decision_engine.models import TradeDirection
from execution.entry_confirmation import confirm_open_fill
from ml.trade_outcomes import get_live_trade_statistics
from options.models import OptionLeg, OptionStrategy
from options.selection import select_expiration, select_strike_by_delta
from options.strategy_builders import MIN_TRADEABLE_CONTRACT_COST, build_long_call, build_long_put
from risk.halt_manager import evaluate_loss_limits
from risk.sizing import contracts_for_budget, position_budget_dollars
from risk.streak import current_positive_day_streak, streak_adjusted_fraction
from trade_management.close_execution import close_with_price_walk
from trade_management.exit_rules import evaluate_exit
from trade_management.expiry import trading_days_until
from trade_management.models import ExitAction, OpenPositionRecord, PersistedLeg, PositionState
from trade_management.pnl import current_value_per_unit as compute_current_value_per_unit
from utils.time import is_equity_market_open, is_us_market_weekday, minutes_since_equity_open

from .context import AppContext, get_effective_breakout_account
from .option_guards import latest_completed_5m_close, recent_5m_bars, spread_rejection
from .trading_loop import (
    _BARS_LOOKBACK_DAYS,
    _LOOKBACK_DAYS_BY_TIMEFRAME,
    _MIN_BARS_FOR_SIGNAL,
    _SCAN_FUNCTIONS,
    EventCallback,
    _current_contracts_for_legs,
    _emit,
)

logger = logging.getLogger(__name__)

# Breakout Hunter's config, fixed (not .env) — this is an experiment, not a
# tuned live strategy. See decision_engine.scoring.BREAKOUT_WEIGHTS.
_CONFIDENCE_THRESHOLD = 58.0
_TARGET_DELTA = 0.15
_TARGET_DTE = 20  # calendar days, mirroring trading_loop's option_target_dte usage
_SIGNAL_VEHICLE = "options_breakout"
_ASSET_CLASS = "breakout"
_HALT_SCOPE = "breakout"


async def breakout_entry_cycle(
    context: AppContext, now: datetime, on_event: EventCallback = None, timeframe: str = "1Day"
) -> None:
    if not is_equity_market_open(now):
        logger.info("breakout entry cycle (%s) skipped: market closed", timeframe)
        return
    if await context.halt_manager.is_halted(_HALT_SCOPE):
        logger.info("breakout entry cycle (%s) skipped: trading halted", timeframe)
        return
    # See config/settings.py option_entry_open_blackout_minutes.
    blackout = context.settings.option_entry_open_blackout_minutes
    if minutes_since_equity_open(now) < blackout:
        logger.info("breakout entry cycle (%s) skipped: inside the first %d minutes after the open", timeframe, blackout)
        return

    account = await get_effective_breakout_account(context)
    max_price = account.equity * context.pre_trade_checker.max_total_exposure_pct
    symbols = await context.universe_manager.get_universe(now, max_price=max_price)
    logger.info("breakout entry cycle (%s): scanning %d symbols", timeframe, len(symbols))
    for symbol in symbols:
        try:
            await _maybe_enter(context, symbol, now, on_event, timeframe)
        except Exception:
            logger.exception("breakout entry cycle (%s) failed for %s", timeframe, symbol)


async def _maybe_enter(
    context: AppContext, symbol: str, now: datetime, on_event: EventCallback, timeframe: str = "1Day"
) -> None:
    if await context.breakout_position_repository.get(symbol) is not None:
        return  # already have an open breakout position in this symbol
    if await context.stock_position_repository.get(symbol) is not None:
        return  # don't stack options on top of a direct stock position

    lookback_days = _LOOKBACK_DAYS_BY_TIMEFRAME.get(timeframe, _BARS_LOOKBACK_DAYS)
    await context.ingestion_service.ingest_incremental(symbol, timeframe, end=now)
    bars = await context.bars_repository.get_bars(symbol, timeframe, now - timedelta(days=lookback_days), now)
    if len(bars) < _MIN_BARS_FOR_SIGNAL:
        return

    scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, bars)) is not None]
    congress_trades = await context.congress_trade_manager.get_recent_trades(
        symbol, now, lookback_days=context.settings.congress_lookback_days
    )
    signal = context.breakout_decision_model.score(
        symbol, bars, scan_hits, _CONFIDENCE_THRESHOLD,
        congress_trades=congress_trades, tracked_members=context.settings.congress_tracked_members,
    )
    if not signal.meets_threshold or signal.direction is TradeDirection.NEUTRAL:
        await context.signal_confirmation_repository.clear(symbol, _SIGNAL_VEHICLE, timeframe)
        return

    factor_values = {f.name: f.value for f in signal.factors}
    await context.feature_store_repository.record_snapshot(
        symbol, now, factor_values, signal.confidence, signal.direction.value
    )

    confirmation = await context.signal_confirmation_repository.get(symbol, _SIGNAL_VEHICLE, timeframe)
    previous_direction = confirmation.direction if confirmation else None
    previous_streak = confirmation.streak if confirmation else 0
    streak = update_streak(signal.direction, previous_direction, previous_streak)
    await context.signal_confirmation_repository.upsert(symbol, _SIGNAL_VEHICLE, timeframe, signal.direction, streak, now)
    if not is_confirmed(streak, context.settings.signal_confirmation_count):
        logger.info(
            "breakout entry cycle (%s) skipped %s: signal %s met threshold but awaiting confirmation (%d/%d)",
            timeframe, symbol, signal.direction.value, streak, context.settings.signal_confirmation_count,
        )
        return

    right = OptionRight.CALL if signal.direction is TradeDirection.BULLISH else OptionRight.PUT
    target_date = now.date() + timedelta(days=_TARGET_DTE)
    deviation = timedelta(days=context.settings.option_max_dte_deviation_days)
    chain = await context.broker.get_option_chain(
        symbol, expiration_gte=target_date - deviation, expiration_lte=target_date + deviation
    )
    expirations = sorted({c.expiration for c in chain if c.right is right})
    if not expirations:
        logger.info(
            "breakout entry cycle (%s) skipped %s: chain has no %s contracts near target", timeframe, symbol, right.value
        )
        return
    expiration = select_expiration(expirations, _TARGET_DTE, now.date())

    calendar_dte = (expiration - now.date()).days
    if abs(calendar_dte - _TARGET_DTE) > context.settings.option_max_dte_deviation_days:
        logger.info(
            "breakout entry cycle (%s) skipped %s: nearest expiration %s is %d days out, too far from target %d",
            timeframe, symbol, expiration, calendar_dte, _TARGET_DTE,
        )
        return

    dte = trading_days_until(expiration, now.date())
    if dte <= context.trade_management_config.min_trading_days_before_expiry:
        logger.info(
            "breakout entry cycle (%s) skipped %s: expiration %s only %d trading days out",
            timeframe, symbol, expiration, dte,
        )
        return

    candidates = [c for c in chain if c.right is right and c.expiration == expiration]
    target_delta = _TARGET_DELTA if right is OptionRight.CALL else -_TARGET_DELTA
    try:
        contract = select_strike_by_delta(candidates, target_delta)
    except ValueError as exc:
        logger.info(
            "breakout entry cycle (%s) skipped %s: no contract near target delta %.2f (%s)",
            timeframe, symbol, target_delta, exc,
        )
        return

    try:
        strategy = build_long_call(contract) if right is OptionRight.CALL else build_long_put(contract)
    except Exception:
        logger.exception("breakout: failed to build strategy for %s", symbol)
        return

    if strategy.net_debit < MIN_TRADEABLE_CONTRACT_COST:
        logger.info(
            "breakout entry cycle (%s) skipped %s: contract net_debit $%.2f below tradeable floor $%.2f",
            timeframe, symbol, strategy.net_debit, MIN_TRADEABLE_CONTRACT_COST,
        )
        return

    wide = spread_rejection(contract, context.settings.max_entry_spread_pct)
    if wide is not None:
        logger.info("breakout entry cycle (%s) skipped %s: %s", timeframe, symbol, wide)
        return

    # Stop on the underlying, not the option quote (see PositionState.underlying_stop_price).
    bars_5m = await recent_5m_bars(context, symbol, now)
    stop_level = underlying_stop_level(
        bars_5m, signal.direction, now, context.settings.underlying_stop_atr_multiple
    )
    if stop_level is None:
        logger.info("breakout entry cycle (%s) skipped %s: not enough 5Min history to set the underlying stop", timeframe, symbol)
        return
    entry_underlying_price, underlying_stop_price = stop_level

    async with context.breakout_entry_lock:
        if await context.breakout_position_repository.get(symbol) is not None:
            return
        if await context.stock_position_repository.get(symbol) is not None:
            return

        account = await get_effective_breakout_account(context)
        stats = await get_live_trade_statistics(context.trade_outcome_repository, asset_class=_ASSET_CLASS)
        kelly_result = context.breakout_kelly_sizer.size(stats)
        daily_pnls = await context.trade_outcome_repository.daily_pnls(asset_class=_ASSET_CLASS)
        positive_day_streak = current_positive_day_streak(daily_pnls)
        kelly_result = replace(
            kelly_result, position_fraction=streak_adjusted_fraction(kelly_result.position_fraction, positive_day_streak)
        )
        budget = position_budget_dollars(account.equity, kelly_result)
        qty = contracts_for_budget(budget, strategy.net_debit)
        if qty <= 0:
            logger.info(
                "breakout entry cycle (%s) skipped %s: budget $%.2f can't afford one contract at net_debit $%.2f",
                timeframe, symbol, budget, strategy.net_debit,
            )
            return

        # Scoped to this sleeve's own tracked contract symbols -- see
        # risk.pre_trade.PreTradeChecker and project memory on the INTC
        # trade: account.equity here is this sleeve's small synthetic
        # sub-balance, not the real combined account, so checking it against
        # every sleeve's positions (or an untracked stray one) blows the
        # exposure cap for a reason that has nothing to do with this sleeve.
        held_symbols = {leg.symbol for r in await context.breakout_position_repository.get_all() for leg in r.legs}
        positions = [p for p in await context.broker.get_positions() if p.symbol in held_symbols]
        estimated_cost = qty * strategy.net_debit
        check = await context.pre_trade_checker.evaluate(account, positions, symbol, estimated_cost)
        if not check.passed:
            failed = [f"{c.name}: {c.reason}" for c in check.checks if not c.passed]
            logger.info(
                "breakout entry cycle (%s) skipped %s: pre-trade check failed (%s)", timeframe, symbol, "; ".join(failed)
            )
            return

        result = await context.executor.execute(strategy, qty)
        fill = await confirm_open_fill(context.executor, context.broker, result.order.order_id, symbol)
        if not fill.filled:
            return  # nothing to track; the next entry cycle can try again
        qty = int(fill.filled_qty)

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
            state=PositionState(
                symbol=symbol, qty=qty, entry_cost_per_unit=strategy.net_debit, scaled_out=False, peak_gain_pct=0.0,
                entry_underlying_price=entry_underlying_price, underlying_stop_price=underlying_stop_price,
            ),
        )
        await context.breakout_position_repository.upsert(record, updated_at=now)

    await context.signal_confirmation_repository.clear(symbol, _SIGNAL_VEHICLE, timeframe)
    await context.alert_manager.send(
        Alert(
            title=f"[breakout] Opened {strategy.strategy_type.value} on {symbol}",
            message=(
                f"qty={qty} entry_cost={strategy.net_debit:.2f} confidence={signal.confidence:.1f} "
                f"timeframe={timeframe} order={result.order.order_id}"
            ),
            severity=Severity.INFO,
            timestamp=now,
        )
    )
    await _emit(
        on_event,
        {"type": "breakout_position_opened", "symbol": symbol, "qty": qty, "entry_cost": strategy.net_debit, "timeframe": timeframe},
    )


async def breakout_position_management_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    if not is_equity_market_open(now):
        logger.info("breakout position management cycle skipped: market closed")
        return

    records = await context.breakout_position_repository.get_all()
    logger.info("breakout position management cycle: checking %d tracked positions", len(records))
    for record in records:
        try:
            await _manage_position(context, record, now, on_event)
        except Exception:
            logger.exception("breakout position management failed for %s", record.symbol)


async def _current_signal(context: AppContext, symbol: str, now: datetime) -> tuple[TradeDirection | None, float | None]:
    """Breakout's reversal-exit / conviction-hold re-score — same shape as
    trading_loop's, but against the breakout model / threshold. Returns
    (direction, confidence): direction feeds the reversal-exit check same as
    before; confidence feeds evaluate_exit's conviction-hold override (see
    TradeManagementConfig.conviction_hold_confidence_floor) so a position can
    outlive max_hold_trading_days for as long as this re-score keeps liking
    it, instead of on a blind calendar cutoff. (None, None) means the caller
    couldn't re-score this cycle (insufficient bar history) -- both checks
    treat that as "no fresh read," not as license to hold or reason to exit."""
    await context.ingestion_service.ingest_incremental(symbol, "1Day", end=now)
    bars = await context.bars_repository.get_bars(symbol, "1Day", now - timedelta(days=_BARS_LOOKBACK_DAYS), now)
    if len(bars) < _MIN_BARS_FOR_SIGNAL:
        return None, None
    scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, bars)) is not None]
    congress_trades = await context.congress_trade_manager.get_recent_trades(
        symbol, now, lookback_days=context.settings.congress_lookback_days
    )
    signal = context.breakout_decision_model.score(
        symbol, bars, scan_hits, _CONFIDENCE_THRESHOLD,
        congress_trades=congress_trades, tracked_members=context.settings.congress_tracked_members,
    )
    if not signal.meets_threshold:
        return TradeDirection.NEUTRAL, signal.confidence
    return signal.direction, signal.confidence


async def _manage_position(context: AppContext, record: OpenPositionRecord, now: datetime, on_event: EventCallback) -> None:
    current_contracts = await _current_contracts_for_legs(context, record)
    if len(current_contracts) < len(record.legs):
        logger.warning("breakout: missing current quotes for some legs of %s, skipping this cycle", record.symbol)
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
    current_direction, current_confidence = await _current_signal(context, record.symbol, now)

    underlying_close = (
        await latest_completed_5m_close(context, record.symbol, now)
        if record.state.underlying_stop_price is not None else None
    )

    decision = evaluate_exit(
        record.state, current_value, dte, context.trade_management_config,
        current_direction=current_direction, entry_direction=record.direction,
        trading_days_held=trading_days_until(now.date(), record.entry_date),
        current_confidence=current_confidence,
        underlying_close=underlying_close,
    )
    if decision.action is ExitAction.NONE:
        if (
            decision.stop_loss_streak != record.state.stop_loss_streak
            or decision.reversal_streak != record.state.reversal_streak
            or decision.trailing_stop_streak != record.state.trailing_stop_streak
            or decision.catastrophic_streak != record.state.catastrophic_streak
        ):
            updated_state = replace(
                record.state, stop_loss_streak=decision.stop_loss_streak, reversal_streak=decision.reversal_streak,
                trailing_stop_streak=decision.trailing_stop_streak, catastrophic_streak=decision.catastrophic_streak,
            )
            await context.breakout_position_repository.upsert(replace(record, state=updated_state), updated_at=now)
        return

    fill = await close_with_price_walk(
        context.broker, context.executor, strategy, decision.qty_to_close, current_contracts, record.symbol
    )
    if not fill.filled:
        return  # position stays tracked as-is; next cycle re-evaluates and retries

    pnl = (current_value - record.state.entry_cost_per_unit) * fill.filled_qty
    await context.trade_outcome_repository.record_outcome(record.symbol, now, pnl, asset_class=_ASSET_CLASS)

    remaining = record.state.qty - fill.filled_qty
    if remaining <= 0:
        await context.breakout_position_repository.delete(record.symbol)
    else:
        current_gain_pct = (current_value - record.state.entry_cost_per_unit) / record.state.entry_cost_per_unit
        peak = max(record.state.peak_gain_pct, current_gain_pct)
        updated_state = replace(
            record.state, qty=remaining, scaled_out=True,
            peak_gain_pct=peak, stop_loss_streak=decision.stop_loss_streak, reversal_streak=decision.reversal_streak,
            trailing_stop_streak=decision.trailing_stop_streak, catastrophic_streak=decision.catastrophic_streak,
        )
        await context.breakout_position_repository.upsert(replace(record, state=updated_state), updated_at=now)

    severity = Severity.WARNING if decision.action in (ExitAction.STOP_LOSS, ExitAction.REVERSAL_EXIT) else Severity.INFO
    await context.alert_manager.send(
        Alert(
            title=f"[breakout] {decision.action.value} on {record.symbol}",
            message=f"{decision.reason} pnl={pnl:.2f}",
            severity=severity,
            timestamp=now,
        )
    )
    await _emit(
        on_event, {"type": "breakout_position_closed", "symbol": record.symbol, "action": decision.action.value, "pnl": pnl}
    )


async def breakout_loss_limit_check_cycle(context: AppContext, now: datetime) -> None:
    """Breakout's own daily/weekly loss-limit circuit breaker, scoped to
    asset_class / halt scope "breakout" — same policy and paper-mode
    notify-only behavior as trading_loop.loss_limit_check_cycle."""
    if await context.halt_manager.is_halted(_HALT_SCOPE):
        return

    account = await get_effective_breakout_account(context)
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
                    title="Trading halted (breakout): loss limit breached",
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
                    title="Loss limit breached (breakout, paper trading — not halted)",
                    message=f"{breach_reason}; daily_pnl_pct={daily_pnl_pct:.2%} weekly_pnl_pct={weekly_pnl_pct:.2%}",
                    severity=Severity.WARNING,
                    timestamp=now,
                    dedup_key="loss-limit-breach-breakout-paper",
                )
            )


async def breakout_progress_report_cycle(context: AppContext, now: datetime) -> None:
    """Discord status ping for the breakout strategy, mirroring
    trading_loop.progress_report_cycle."""
    if context.progress_notifier is None:
        return
    if not is_us_market_weekday(now):
        return

    account = await get_effective_breakout_account(context)
    positions = await context.breakout_position_repository.get_all()
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
        Alert(title="Breakout progress", message="\n".join(lines), severity=Severity.INFO, timestamp=now)
    )
