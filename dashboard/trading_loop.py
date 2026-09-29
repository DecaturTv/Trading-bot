import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

from alerts.models import Alert, Severity
from broker.models import OptionRight
from decision_engine.confirmation import is_confirmed, update_streak
from decision_engine.entry_timing import chase_rejection, drop_forming_bar, underlying_stop_level
from decision_engine.models import TradeDirection
from execution.entry_confirmation import confirm_open_fill
from ml.trade_outcomes import get_live_trade_statistics
from options.models import OptionLeg, OptionStrategy
from options.selection import select_expiration, select_strike_by_delta
from options.strategy_builders import MIN_TRADEABLE_CONTRACT_COST, build_long_call, build_long_put
from risk.halt_manager import evaluate_loss_limits
from risk.sizing import contracts_for_budget, position_budget_dollars
from risk.streak import current_positive_day_streak, streak_adjusted_fraction
from scanner.scans import scan_gap, scan_momentum, scan_unusual_volume
from trade_management.close_execution import close_with_price_walk
from trade_management.exit_rules import evaluate_exit
from trade_management.expiry import trading_days_until
from trade_management.models import ExitAction, OpenPositionRecord, PersistedLeg, PositionState
from trade_management.pnl import current_value_per_unit as compute_current_value_per_unit
from utils.time import is_equity_market_open, is_us_market_weekday, minutes_since_equity_open

from .context import AppContext, get_effective_account
from .option_guards import latest_completed_5m_close, recent_5m_bars, spread_rejection

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
_SIGNAL_VEHICLE = "options"

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
    blackout = context.settings.option_entry_open_blackout_minutes
    if minutes_since_equity_open(now) < blackout:
        logger.info("entry cycle (%s) skipped: inside the first %d minutes after the open", timeframe, blackout)
        return

    account = await get_effective_account(context)
    # Underlying price is a proxy, not the real contract cost (net_debit) --
    # good enough to drop names structurally too expensive to size into at
    # all (MSFT/SPY/QQQ/MU-class prices) without fetching a full chain for
    # every universe symbol just to filter.
    max_price = account.equity * context.pre_trade_checker.max_total_exposure_pct
    symbols = await context.universe_manager.get_universe(now, max_price=max_price)
    logger.info("entry cycle (%s): scanning %d symbols", timeframe, len(symbols))
    for symbol in symbols:
        try:
            await _maybe_enter(context, symbol, now, on_event, timeframe)
        except Exception:
            logger.exception("entry cycle (%s) failed for %s", timeframe, symbol)


async def _maybe_enter(context: AppContext, symbol: str, now: datetime, on_event: EventCallback, timeframe: str = "1Day") -> None:
    if await context.position_repository.get(symbol) is not None:
        return  # already have an open options position in this symbol
    if await context.stock_position_repository.get(symbol) is not None:
        return  # already holding this symbol as a direct stock position instead

    lookback_days = _LOOKBACK_DAYS_BY_TIMEFRAME.get(timeframe, _BARS_LOOKBACK_DAYS)
    await context.ingestion_service.ingest_incremental(symbol, timeframe, end=now)
    raw_bars = await context.bars_repository.get_bars(symbol, timeframe, now - timedelta(days=lookback_days), now)
    # Score completed bars only, so a signal (and its confirmation streak
    # below) can't flicker with a bar that's still forming.
    bars = drop_forming_bar(raw_bars, timeframe, now)
    if len(bars) < _MIN_BARS_FOR_SIGNAL:
        return

    scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, bars)) is not None]
    congress_trades = await context.congress_trade_manager.get_recent_trades(
        symbol, now, lookback_days=context.settings.congress_lookback_days
    )
    signal = context.decision_model.score(
        symbol, bars, scan_hits, context.settings.confidence_threshold,
        congress_trades=congress_trades, tracked_members=context.settings.congress_tracked_members,
    )
    if not signal.meets_threshold or signal.direction is TradeDirection.NEUTRAL:
        await context.signal_confirmation_repository.clear(symbol, _SIGNAL_VEHICLE, timeframe)
        return

    # Snapshot the confidence/factors at scan time, independent of whether
    # this signal goes on to confirm and enter. bars get upserted in place as
    # ingestion catches up (see data/bars_repository.py), so replaying a past
    # scan against today's stored bars can silently diverge from what was
    # actually live -- this is the only faithful record of what a near-miss
    # (or a confirmed entry) actually scored.
    factor_values = {f.name: f.value for f in signal.factors}
    await context.feature_store_repository.record_snapshot(
        symbol, now, factor_values, signal.confidence, signal.direction.value
    )

    # Require the signal to hold for signal_confirmation_count consecutive
    # completed bars (on this timeframe) before acting on it, as the
    # backtest does -- a single noisy bar shouldn't be enough to open a
    # position. The streak used to count scans: with the 5Min cycle polling
    # every 60s, "3 confirmations" was ~2 minutes on one still-forming bar,
    # and on 2026-09-29 every entry fired 09:31-09:38. updated_at stores the
    # last bar counted, so rescanning the same bar doesn't advance it. See
    # config/settings.py signal_confirmation_count.
    confirmation = await context.signal_confirmation_repository.get(symbol, _SIGNAL_VEHICLE, timeframe)
    last_bar_at = bars[-1].timestamp
    if confirmation and confirmation.direction == signal.direction and confirmation.updated_at >= last_bar_at:
        streak = confirmation.streak
    else:
        previous_direction = confirmation.direction if confirmation else None
        previous_streak = confirmation.streak if confirmation else 0
        streak = update_streak(signal.direction, previous_direction, previous_streak)
        await context.signal_confirmation_repository.upsert(
            symbol, _SIGNAL_VEHICLE, timeframe, signal.direction, streak, last_bar_at
        )
    if not is_confirmed(streak, context.settings.signal_confirmation_count):
        logger.info(
            "entry cycle (%s) skipped %s: signal %s met threshold but awaiting confirmation (%d/%d)",
            timeframe, symbol, signal.direction.value, streak, context.settings.signal_confirmation_count,
        )
        return

    # Don't buy the top of a move that already happened: wait for the
    # underlying to pull back or consolidate. The confirmed streak is kept, so
    # the entry is retried on later scans. See decision_engine/entry_timing.py.
    bars_5m = raw_bars if timeframe == "5Min" else await recent_5m_bars(context, symbol, now)
    chasing = chase_rejection(
        bars_5m, signal.direction, now,
        context.settings.entry_max_extension_atr, context.settings.entry_max_range_position,
    )
    if chasing is not None:
        logger.info("entry cycle (%s) skipped %s: %s entry would chase the move -- %s",
                    timeframe, symbol, signal.direction.value, chasing)
        return

    right = OptionRight.CALL if signal.direction is TradeDirection.BULLISH else OptionRight.PUT
    # Bound the request to the window select_expiration() could actually use --
    # anything outside +/- option_max_dte_deviation_days of the target would be
    # rejected below anyway. Fetching unbounded-from-today instead relies on
    # Alpaca's pagination to page past every near-term expiration first, and for
    # high-strike-count/high-expiration-frequency underlyings (e.g. SPY's
    # near-daily expirations, 100+ strikes each) that can exhaust the chain
    # fetch's page budget before it ever reaches something near the target DTE.
    # Confirmed live 2026-08-19: the far-dated expiration was real and tradable,
    # the unbounded fetch just never got to it.
    target_date = now.date() + timedelta(days=context.settings.option_target_dte)
    deviation = timedelta(days=context.settings.option_max_dte_deviation_days)
    chain = await context.broker.get_option_chain(
        symbol, expiration_gte=target_date - deviation, expiration_lte=target_date + deviation
    )
    expirations = sorted({c.expiration for c in chain if c.right is right})
    if not expirations:
        logger.info(
            "entry cycle (%s) skipped %s: signal met threshold (confidence=%.1f) but option chain has no %s contracts",
            timeframe, symbol, signal.confidence, right.value,
        )
        return
    expiration = select_expiration(expirations, context.settings.option_target_dte, now.date())

    # select_expiration picks the closest available expiration to the target
    # regardless of how close that is -- if the chain has nothing near
    # option_target_dte, it hands back whatever's closest anyway. That can be
    # a contract days from expiry when 25 DTE was wanted (see the ACI trade
    # in project memory: target 25 DTE, chain only had ~3 DTE available,
    # resulting in a deep-theta/gamma contract whose price swings 50%+ on
    # quote noise alone). Skip the entry rather than take a contract whose
    # risk profile doesn't match what was configured.
    calendar_dte = (expiration - now.date()).days
    dte_deviation = abs(calendar_dte - context.settings.option_target_dte)
    if dte_deviation > context.settings.option_max_dte_deviation_days:
        logger.info(
            "entry cycle (%s) skipped %s: nearest expiration %s is %d calendar days out, "
            "%d away from target %d (> %d max deviation)",
            timeframe, symbol, expiration, calendar_dte, dte_deviation,
            context.settings.option_target_dte, context.settings.option_max_dte_deviation_days,
        )
        return

    # Separate floor: even within the deviation tolerance above, never open a
    # position that's already at or past the force-close line -- it would
    # open and immediately EXPIRY_EXIT on the next position-management pass.
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
    except ValueError as exc:
        logger.info(
            "entry cycle (%s) skipped %s: no contract near target delta %.2f among %d candidates (%s)",
            timeframe, symbol, target_delta, len(candidates), exc,
        )
        return

    try:
        strategy = build_long_call(contract) if right is OptionRight.CALL else build_long_put(contract)
    except Exception:
        logger.exception("failed to build strategy for %s", symbol)
        return

    # Reject sub-$1/contract options before we ever size or fill one. The
    # synthetic-pricing blow-up this guards against in the tournament
    # (tournament/runner.py) has a live twin: a deep-OTM contract with a
    # near-zero premium has no depth behind its quote, so stops/scale-outs
    # can't fill, and `budget // net_debit` sizes a huge lottery-ticket
    # stack that loses its whole value in a day (see the BITO -$520 day).
    if strategy.net_debit < MIN_TRADEABLE_CONTRACT_COST:
        logger.info(
            "entry cycle (%s) skipped %s: contract net_debit $%.2f below tradeable floor $%.2f",
            timeframe, symbol, strategy.net_debit, MIN_TRADEABLE_CONTRACT_COST,
        )
        return

    wide = spread_rejection(contract, context.settings.max_entry_spread_pct)
    if wide is not None:
        logger.info("entry cycle (%s) skipped %s: %s", timeframe, symbol, wide)
        return

    # Stop on the underlying, not the option quote (see PositionState.underlying_stop_price).
    stop_level = underlying_stop_level(
        bars_5m, signal.direction, now, context.settings.underlying_stop_atr_multiple
    )
    if stop_level is None:
        logger.info("entry cycle (%s) skipped %s: not enough 5Min history to set the underlying stop", timeframe, symbol)
        return
    entry_underlying_price, underlying_stop_price = stop_level

    # Everything above this point is read-only (bars, signal, chain, strike)
    # and safe to run concurrently across the 5m/15m/1h/1d option cycles and
    # the stock entry cycle. From here on we're committing real capital
    # against one shared Alpaca account/budget, so it's serialized: without
    # this, two of those cycles could both evaluate exposure against the same
    # stale get_positions() snapshot and collectively overcommit (see the
    # AAL/TSLA/TSLL incident this fixes).
    async with context.equities_entry_lock:
        if await context.position_repository.get(symbol) is not None:
            return  # opened by a concurrent entry cycle while we were scanning
        if await context.stock_position_repository.get(symbol) is not None:
            return

        account = await get_effective_account(context)
        stats = await get_live_trade_statistics(context.trade_outcome_repository, asset_class="equities")
        kelly_result = context.kelly_sizer.size(stats)
        daily_pnls = await context.trade_outcome_repository.daily_pnls(asset_class="equities")
        positive_day_streak = current_positive_day_streak(daily_pnls)
        kelly_result = replace(
            kelly_result, position_fraction=streak_adjusted_fraction(kelly_result.position_fraction, positive_day_streak)
        )
        budget = position_budget_dollars(account.equity, kelly_result)
        qty = contracts_for_budget(budget, strategy.net_debit)
        if qty <= 0:
            logger.info(
                "entry cycle (%s) skipped %s: budget $%.2f can't afford one contract at net_debit $%.2f",
                timeframe, symbol, budget, strategy.net_debit,
            )
            return

        # estimated_cost must be the full qty x net_debit that's about to be
        # committed, not a single contract's price -- checking against one
        # unit let buying-power/exposure pass trades that, once actually
        # sized, committed far more capital than the cap intended (see
        # project memory on the stock-entries-blocked-by-exposure diagnosis,
        # which traced runaway exposure back to this same per-unit check).
        # Scoped to this sleeve's own tracked contract symbols -- see
        # risk.pre_trade.PreTradeChecker and project memory on the INTC
        # trade: account.equity here is this sleeve's small synthetic
        # sub-balance, not the real combined account, so checking it against
        # every sleeve's positions (or an untracked stray one) blows the
        # exposure cap for a reason that has nothing to do with this sleeve.
        held_symbols = {leg.symbol for r in await context.position_repository.get_all() for leg in r.legs}
        positions = [p for p in await context.broker.get_positions() if p.symbol in held_symbols]
        estimated_cost = qty * strategy.net_debit
        check = await context.pre_trade_checker.evaluate(account, positions, symbol, estimated_cost)
        if not check.passed:
            failed = [f"{c.name}: {c.reason}" for c in check.checks if not c.passed]
            logger.info("entry cycle (%s) skipped %s: pre-trade check failed (%s)", timeframe, symbol, "; ".join(failed))
            return

        result = await context.executor.execute(strategy, qty)
        fill = await confirm_open_fill(context.executor, context.broker, result.order.order_id, symbol)
        if not fill.filled:
            return  # nothing to track; a still-confirmed signal retries next cycle
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
        await context.position_repository.upsert(record, updated_at=now)

    await context.signal_confirmation_repository.clear(symbol, _SIGNAL_VEHICLE, timeframe)
    await context.alert_manager.send(
        Alert(
            title=f"Opened {strategy.strategy_type.value} on {symbol}",
            message=(
                f"qty={qty} entry_cost={strategy.net_debit:.2f} confidence={signal.confidence:.1f} "
                f"underlying_stop={underlying_stop_price:.2f} timeframe={timeframe} order={result.order.order_id}"
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


async def _current_signal(context: AppContext, symbol: str, now: datetime) -> tuple[TradeDirection | None, float | None]:
    """Re-scores symbol's current daily signal for the reversal-exit /
    conviction-hold checks on an open position. Returns (None, None) (skip
    both checks this cycle) if there isn't enough bar history yet; direction
    comes back NEUTRAL if the signal doesn't meet the confidence threshold,
    so a low-conviction flicker doesn't count as a confirmed reversal any
    more than it would count as a confirmed entry -- and NEUTRAL never
    matches a real entry_direction, so it can't trigger a conviction-hold
    reprieve either. confidence feeds evaluate_exit's conviction-hold
    override (TradeManagementConfig.conviction_hold_confidence_floor): a
    position can outlive max_hold_trading_days for as long as this re-score
    keeps liking it, instead of on a blind calendar cutoff. Always uses the
    daily timeframe regardless of which intraday cycle originally opened the
    position -- OpenPositionRecord doesn't track that, and daily trend is a
    reasonable, simpler basis for "has the thesis actually reversed/faded"
    than re-deriving the entry timeframe."""
    await context.ingestion_service.ingest_incremental(symbol, "1Day", end=now)
    bars = await context.bars_repository.get_bars(symbol, "1Day", now - timedelta(days=_BARS_LOOKBACK_DAYS), now)
    if len(bars) < _MIN_BARS_FOR_SIGNAL:
        return None, None

    scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, bars)) is not None]
    congress_trades = await context.congress_trade_manager.get_recent_trades(
        symbol, now, lookback_days=context.settings.congress_lookback_days
    )
    signal = context.decision_model.score(
        symbol, bars, scan_hits, context.settings.confidence_threshold,
        congress_trades=congress_trades, tracked_members=context.settings.congress_tracked_members,
    )
    if not signal.meets_threshold:
        return TradeDirection.NEUTRAL, signal.confidence
    return signal.direction, signal.confidence


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
            await context.position_repository.upsert(replace(record, state=updated_state), updated_at=now)
        return

    fill = await close_with_price_walk(
        context.broker, context.executor, strategy, decision.qty_to_close, current_contracts, record.symbol
    )
    if not fill.filled:
        return  # position stays tracked as-is; next cycle re-evaluates and retries

    pnl = (current_value - record.state.entry_cost_per_unit) * fill.filled_qty
    await context.trade_outcome_repository.record_outcome(record.symbol, now, pnl, asset_class="equities")

    remaining = record.state.qty - fill.filled_qty
    if remaining <= 0:
        await context.position_repository.delete(record.symbol)
    else:
        current_gain_pct = (current_value - record.state.entry_cost_per_unit) / record.state.entry_cost_per_unit
        peak = max(record.state.peak_gain_pct, current_gain_pct)
        updated_state = replace(
            record.state, qty=remaining, scaled_out=True,
            peak_gain_pct=peak, stop_loss_streak=decision.stop_loss_streak, reversal_streak=decision.reversal_streak,
            trailing_stop_streak=decision.trailing_stop_streak, catastrophic_streak=decision.catastrophic_streak,
        )
        await context.position_repository.upsert(replace(record, state=updated_state), updated_at=now)

    severity = Severity.WARNING if decision.action in (ExitAction.STOP_LOSS, ExitAction.REVERSAL_EXIT) else Severity.INFO
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

    Paper trading skips the weekly check -- the point of paper trading is to
    take a bad day, learn from it, and keep going, not carry a rolling
    weekly drag from bugs already fixed.

    Only live trading actually halts on a breach (HaltManager.is_halted is
    hard-wired to False in paper mode). Paper trading evaluates the exact
    same daily_loss_limit_pct/weekly_loss_limit_pct thresholds but only
    notifies (what would have halted, and why) -- there's no real
    capital to protect, and letting a bad paper day keep running gives more
    signal on whether a fix (e.g. the reversal-confirmation change) actually
    helps than cutting the day short would. Live trading keeps the full
    daily+weekly protection: once real capital is on the line, a bad week
    matters even the day after a good one.
    """
    if await context.halt_manager.is_halted("equities"):
        return

    account = await get_effective_account(context)
    if account.equity <= 0:
        return

    day_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    daily_pnl_pct = sum(await context.trade_outcome_repository.pnls_since(day_start, asset_class="equities")) / account.equity

    if context.settings.trading_mode == "paper":
        weekly_pnl_pct = 0.0
    else:
        week_start = day_start - timedelta(days=now.weekday())
        weekly_pnl_pct = sum(await context.trade_outcome_repository.pnls_since(week_start, asset_class="equities")) / account.equity

    if context.settings.trading_mode == "live":
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
    else:
        breach_reason = evaluate_loss_limits(
            daily_pnl_pct, weekly_pnl_pct, context.settings.daily_loss_limit_pct, context.settings.weekly_loss_limit_pct
        )
        if breach_reason is not None:
            await context.alert_manager.send(
                Alert(
                    title="Loss limit breached (equities, paper trading — not halted)",
                    message=f"{breach_reason}; daily_pnl_pct={daily_pnl_pct:.2%} weekly_pnl_pct={weekly_pnl_pct:.2%}",
                    severity=Severity.WARNING,
                    timestamp=now,
                    # This cycle re-checks every position_check_interval and
                    # paper mode never halts, so without dedup the breach
                    # re-alerts every ~2 min for the rest of the day.
                    dedup_key="loss-limit-breach-equities-paper",
                )
            )


async def progress_report_cycle(context: AppContext, now: datetime) -> None:
    """Discord status ping for the equities/options side — separate from the
    severity-gated AlertManager channels since this is a routine update, not
    an event alert. Scheduled twice a trading day (midday + shortly after the
    close), so it gates on the weekday, not on the market currently being
    open. No-ops if Discord isn't configured. See forex_progress_report_cycle
    for the forex counterpart, sent as its own alert."""
    if context.progress_notifier is None:
        return
    if not is_us_market_weekday(now):
        return

    account = await get_effective_account(context)
    positions = await context.position_repository.get_all()
    stock_positions = await context.stock_position_repository.get_all()
    halted = await context.halt_manager.is_halted("equities")

    day_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    daily_pnl = sum(await context.trade_outcome_repository.pnls_since(day_start, asset_class="equities"))
    cumulative_pnl = sum(await context.trade_outcome_repository.recent_pnls(asset_class="equities"))
    closed_today = [
        trade
        for trade in await context.trade_outcome_repository.recent_trades(limit=50, asset_class="equities")
        if trade["closed_at"] >= day_start
    ]

    lines = [
        f"equity=${account.equity:,.2f} day_pnl=${daily_pnl:,.2f} cumulative_pnl=${cumulative_pnl:,.2f} "
        f"open_options_positions={len(positions)} open_stock_positions={len(stock_positions)} "
        f"status={'HALTED' if halted else 'running'}"
    ]

    if positions or stock_positions:
        lines.append("\nOpen positions:")
        lines.extend(
            f"- {p.symbol} ({p.strategy_type.value}) qty={p.state.qty} entry=${p.state.entry_cost_per_unit:.2f}"
            for p in positions
        )
        lines.extend(
            f"- {p.symbol} (stock) qty={p.state.qty} entry=${p.state.entry_cost_per_unit:.2f}" for p in stock_positions
        )

    if closed_today:
        lines.append("\nClosed today:")
        lines.extend(f"- {trade['symbol']} pnl={trade['pnl']:+.2f}" for trade in closed_today)

    message = "\n".join(lines)
    await context.progress_notifier.send(
        Alert(title="Stocks progress", message=message, severity=Severity.INFO, timestamp=now)
    )
