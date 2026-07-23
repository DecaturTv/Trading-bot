from datetime import date, datetime, timedelta, timezone

import pytest
from dash_factories import make_account, make_bars, make_context, make_position_record

from broker.models import OptionContract, OptionGreeks, OptionRight
from dashboard.trading_loop import entry_cycle, loss_limit_check_cycle, position_management_cycle, progress_report_cycle
from decision_engine.models import FactorScore, TradeDirection, TradeSignal
from risk.kelly import KellyResult

MARKET_OPEN_TUESDAY = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)  # ~11am ET, a Tuesday
MARKET_CLOSED_SATURDAY = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)
EXPIRY = date(2026, 9, 18)


def make_chain(strikes_deltas, right=OptionRight.CALL, expiration=EXPIRY):
    return [
        OptionContract(
            symbol=f"AAPL{expiration.strftime('%y%m%d')}{'C' if right is OptionRight.CALL else 'P'}{int(strike*1000):08d}",
            underlying_symbol="AAPL", strike=strike, expiration=expiration, right=right,
            bid=5.0, ask=5.2, last_price=5.1, implied_volatility=0.3,
            greeks=OptionGreeks(delta=delta, gamma=0.02, theta=-0.05, vega=0.1, rho=0.01),
        )
        for strike, delta in strikes_deltas
    ]


def bullish_signal(confidence=95.0):
    return TradeSignal(
        symbol="AAPL", direction=TradeDirection.BULLISH, confidence=confidence,
        factors=[FactorScore(name="momentum", value=0.9, weight=1.0)], meets_threshold=confidence >= 92,
    )


def neutral_signal():
    return TradeSignal(symbol="AAPL", direction=TradeDirection.NEUTRAL, confidence=0.0, factors=[], meets_threshold=False)


class _PassingCheck:
    passed = True


class _FailingCheck:
    passed = False


@pytest.mark.asyncio
async def test_entry_cycle_noop_when_market_closed():
    context = make_context()
    await entry_cycle(context, MARKET_CLOSED_SATURDAY)
    context.universe_manager.get_universe.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_noop_when_halted():
    context = make_context()
    context.halt_manager.is_halted.return_value = True
    await entry_cycle(context, MARKET_OPEN_TUESDAY)
    context.universe_manager.get_universe.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_symbol_with_existing_position():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.position_repository.get.return_value = make_position_record(symbol="AAPL")

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.bars_repository.get_bars.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_insufficient_bars():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=5)

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.decision_model.score.assert_not_called()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_signal_does_not_meet_threshold():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = neutral_signal()

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.broker.get_option_chain.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_no_matching_expiration():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.broker.get_option_chain.return_value = []

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_nearest_expiration_is_too_close_to_dte_floor():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    # MARKET_OPEN_TUESDAY is 2026-07-21; 2026-07-22 is 1 trading day out,
    # <= min_trading_days_before_expiry=2 -- should skip rather than open a
    # position that would immediately force-close on the next check.
    near_expiration = date(2026, 7, 22)
    context.broker.get_option_chain.return_value = make_chain([(95, 0.65), (100, 0.50), (105, 0.35)], expiration=near_expiration)

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_pre_trade_check_fails():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.broker.get_option_chain.return_value = make_chain([(95, 0.65), (100, 0.50), (105, 0.35)])
    context.pre_trade_checker.evaluate.return_value = _FailingCheck()

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_kelly_sizing_yields_zero_qty():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.broker.get_option_chain.return_value = make_chain([(95, 0.65), (100, 0.50), (105, 0.35)])
    context.pre_trade_checker.evaluate.return_value = _PassingCheck()
    context.kelly_sizer.size.return_value = KellyResult(full_kelly_fraction=0.0, position_fraction=0.0, used_fallback=True)

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_happy_path_opens_position():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.broker.get_option_chain.return_value = make_chain([(95, 0.65), (100, 0.50), (105, 0.35)])
    context.pre_trade_checker.evaluate.return_value = _PassingCheck()
    context.kelly_sizer.size.return_value = KellyResult(full_kelly_fraction=0.1, position_fraction=0.1, used_fallback=True)

    events = []
    await entry_cycle(context, MARKET_OPEN_TUESDAY, on_event=events.append)

    context.executor.execute.assert_awaited_once()
    context.position_repository.upsert.assert_awaited_once()
    context.alert_manager.send.assert_awaited_once()
    assert events[0]["type"] == "position_opened"
    assert events[0]["symbol"] == "AAPL"
    assert events[0]["timeframe"] == "1Day"


@pytest.mark.asyncio
async def test_entry_cycle_defaults_to_daily_timeframe():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=5)  # too short -> harmless no-op

    await entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.ingestion_service.ingest_incremental.assert_awaited_once_with("AAPL", "1Day", end=MARKET_OPEN_TUESDAY)
    assert context.bars_repository.get_bars.call_args.args[1] == "1Day"


@pytest.mark.asyncio
async def test_entry_cycle_honors_intraday_timeframe():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=5)  # too short -> harmless no-op

    await entry_cycle(context, MARKET_OPEN_TUESDAY, timeframe="5Min")

    context.ingestion_service.ingest_incremental.assert_awaited_once_with("AAPL", "5Min", end=MARKET_OPEN_TUESDAY)
    assert context.bars_repository.get_bars.call_args.args[1] == "5Min"
    lookback_start = context.bars_repository.get_bars.call_args.args[2]
    assert (MARKET_OPEN_TUESDAY - lookback_start).days == 5


@pytest.mark.asyncio
async def test_entry_cycle_continues_after_one_symbol_raises():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["BAD", "AAPL"]

    async def get_bars_side_effect(symbol, *args, **kwargs):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return make_bars(n=40)

    context.bars_repository.get_bars.side_effect = get_bars_side_effect
    context.decision_model.score.return_value = neutral_signal()

    await entry_cycle(context, MARKET_OPEN_TUESDAY)  # must not raise

    assert context.bars_repository.get_bars.await_count == 2


@pytest.mark.asyncio
async def test_position_management_cycle_noop_when_market_closed():
    context = make_context()
    await position_management_cycle(context, MARKET_CLOSED_SATURDAY)
    context.position_repository.get_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_cycle_noop_when_no_positions():
    context = make_context()
    context.position_repository.get_all.return_value = []
    await position_management_cycle(context, MARKET_OPEN_TUESDAY)
    context.broker.get_option_chain.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_cycle_skips_when_quote_missing():
    context = make_context()
    record = make_position_record(symbol="AAPL", qty=2, entry_cost=500.0, expiration=EXPIRY)
    context.position_repository.get_all.return_value = [record]
    context.broker.get_option_chain.return_value = []  # no quotes available

    await position_management_cycle(context, MARKET_OPEN_TUESDAY)

    context.broker.submit_order.assert_not_awaited()
    context.broker.submit_multi_leg_order.assert_not_awaited()
    context.position_repository.upsert.assert_not_awaited()
    context.position_repository.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_cycle_noop_when_no_exit_triggered():
    context = make_context()
    record = make_position_record(symbol="AAPL", qty=2, entry_cost=500.0, expiration=EXPIRY)
    context.position_repository.get_all.return_value = [record]
    # current value equal to entry cost -> flat, no exit condition met
    context.broker.get_option_chain.return_value = make_chain(
        [(150.0, 0.5)], expiration=EXPIRY
    )
    # override the single contract's symbol/bid/ask to match the leg exactly
    leg_symbol = record.legs[0].symbol
    context.broker.get_option_chain.return_value[0] = OptionContract(
        symbol=leg_symbol, underlying_symbol="AAPL", strike=150.0, expiration=EXPIRY, right=OptionRight.CALL,
        bid=4.99, ask=5.01, last_price=5.0, implied_volatility=0.3,
        greeks=OptionGreeks(delta=0.5, gamma=0.02, theta=-0.05, vega=0.1, rho=0.01),
    )

    await position_management_cycle(context, MARKET_OPEN_TUESDAY)

    context.position_repository.delete.assert_not_awaited()
    context.trade_outcome_repository.record_outcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_cycle_full_exit_deletes_position_and_records_outcome():
    context = make_context()
    record = make_position_record(symbol="AAPL", qty=2, entry_cost=500.0, expiration=EXPIRY)
    leg_symbol = record.legs[0].symbol
    context.position_repository.get_all.return_value = [record]
    # value collapsed well past -50% -> stop_loss, full qty closed
    context.broker.get_option_chain.return_value = [
        OptionContract(
            symbol=leg_symbol, underlying_symbol="AAPL", strike=150.0, expiration=EXPIRY, right=OptionRight.CALL,
            bid=0.5, ask=0.6, last_price=0.55, implied_volatility=0.3,
            greeks=OptionGreeks(delta=0.1, gamma=0.02, theta=-0.05, vega=0.1, rho=0.01),
        )
    ]

    events = []
    await position_management_cycle(context, MARKET_OPEN_TUESDAY, on_event=events.append)

    context.broker.submit_order.assert_awaited_once()
    context.trade_outcome_repository.record_outcome.assert_awaited_once()
    context.position_repository.delete.assert_awaited_once_with("AAPL")
    context.position_repository.upsert.assert_not_awaited()
    context.alert_manager.send.assert_awaited_once()
    assert events[0]["type"] == "position_closed"
    assert events[0]["action"] == "stop_loss"


@pytest.mark.asyncio
async def test_loss_limit_check_noop_when_already_halted():
    context = make_context()
    context.halt_manager.is_halted.return_value = True
    await loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)
    context.broker.get_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_loss_limit_check_scopes_halt_and_pnls_to_equities():
    context = make_context()
    context.trade_outcome_repository.pnls_since.return_value = [-10.0]
    context.halt_manager.check_and_halt_on_loss_limits.return_value = False

    await loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.halt_manager.is_halted.assert_awaited_once_with("equities")
    for call in context.trade_outcome_repository.pnls_since.call_args_list:
        assert call.kwargs["asset_class"] == "equities"
    assert context.halt_manager.check_and_halt_on_loss_limits.call_args.kwargs["scope"] == "equities"


@pytest.mark.asyncio
async def test_loss_limit_check_does_not_halt_within_limits():
    context = make_context()
    context.trade_outcome_repository.pnls_since.return_value = [-10.0]
    context.halt_manager.check_and_halt_on_loss_limits.return_value = False

    await loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.alert_manager.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_loss_limit_check_sends_critical_alert_when_triggered():
    context = make_context()
    context.trade_outcome_repository.pnls_since.return_value = [-1000.0]
    context.halt_manager.check_and_halt_on_loss_limits.return_value = True

    await loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.alert_manager.send.assert_awaited_once()
    alert = context.alert_manager.send.call_args.args[0]
    assert alert.severity.value == "critical"


@pytest.mark.asyncio
async def test_progress_report_noop_when_notifier_not_configured():
    context = make_context()
    context.progress_notifier = None
    await progress_report_cycle(context, MARKET_OPEN_TUESDAY)
    context.broker.get_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_report_noop_when_market_closed():
    context = make_context()
    await progress_report_cycle(context, MARKET_CLOSED_SATURDAY)
    context.progress_notifier.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_report_sends_status_message():
    context = make_context()
    context.broker.get_account.return_value = make_account(equity=12345.67)
    context.position_repository.get_all.return_value = [make_position_record(symbol="AAPL")]
    context.halt_manager.is_halted.return_value = False
    context.trade_outcome_repository.pnls_since.return_value = [50.0, -20.0]

    await progress_report_cycle(context, MARKET_OPEN_TUESDAY)

    context.progress_notifier.send.assert_awaited_once()
    alert = context.progress_notifier.send.call_args.args[0]
    assert "12,345.67" in alert.message
    assert "open_options_positions=1" in alert.message
    assert "status=running" in alert.message


@pytest.mark.asyncio
async def test_progress_report_reports_halted_status():
    context = make_context()
    context.halt_manager.is_halted.return_value = True

    await progress_report_cycle(context, MARKET_OPEN_TUESDAY)

    alert = context.progress_notifier.send.call_args.args[0]
    assert "status=HALTED" in alert.message
