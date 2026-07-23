from datetime import datetime, timezone

import pytest
from dash_factories import make_account, make_bars, make_context, make_forex_position

from broker.models import OrderSide
from dashboard.forex_loop import forex_entry_cycle, forex_position_management_cycle, forex_progress_report_cycle
from decision_engine.models import FactorScore, TradeDirection, TradeSignal

MARKET_OPEN_TUESDAY = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)
MARKET_CLOSED_SATURDAY = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)


def bullish_signal(confidence=95.0):
    return TradeSignal(
        symbol="EUR_USD", direction=TradeDirection.BULLISH, confidence=confidence,
        factors=[FactorScore(name="momentum", value=0.9, weight=1.0)], meets_threshold=confidence >= 92,
    )


def neutral_signal():
    return TradeSignal(symbol="EUR_USD", direction=TradeDirection.NEUTRAL, confidence=0.0, factors=[], meets_threshold=False)


@pytest.mark.asyncio
async def test_entry_cycle_noop_when_forex_broker_not_configured():
    context = make_context()
    context.forex_broker = None
    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)
    context.halt_manager.is_halted.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_noop_when_market_closed():
    context = make_context()
    await forex_entry_cycle(context, MARKET_CLOSED_SATURDAY)
    context.halt_manager.is_halted.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_noop_when_halted():
    context = make_context()
    context.halt_manager.is_halted.return_value = True
    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)
    context.forex_position_repository.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_pair_with_existing_position():
    context = make_context()
    context.forex_position_repository.get.return_value = make_forex_position(pair="EUR_USD")

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.forex_broker.get_candles.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_insufficient_candles():
    context = make_context()
    context.forex_broker.get_candles.return_value = make_bars(n=5)

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.decision_model.score.assert_not_called()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_signal_does_not_meet_threshold():
    context = make_context()
    context.forex_broker.get_candles.return_value = make_bars(n=40)
    context.decision_model.score.return_value = neutral_signal()

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.forex_broker.get_pricing.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_happy_path_opens_position():
    context = make_context()
    context.forex_broker.get_candles.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.forex_broker.get_pricing.return_value = (1.0998, 1.1000)
    context.forex_broker.submit_market_order.return_value = "trade-1"
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)

    events = []
    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY, on_event=events.append)

    context.forex_broker.submit_market_order.assert_awaited_once()
    call = context.forex_broker.submit_market_order.call_args
    assert call.args[0] == "EUR_USD"
    assert call.args[2] is OrderSide.BUY

    context.forex_position_repository.upsert.assert_awaited_once()
    context.alert_manager.send.assert_awaited_once()
    assert events[0]["type"] == "forex_position_opened"
    assert events[0]["pair"] == "EUR_USD"


@pytest.mark.asyncio
async def test_entry_cycle_continues_after_one_pair_raises():
    context = make_context()
    context.forex_broker.get_tradeable_pairs.return_value = ["EUR_USD", "GBP_USD"]

    async def get_candles_side_effect(pair, *args, **kwargs):
        if pair == "EUR_USD":
            raise RuntimeError("boom")
        return make_bars(n=5)  # too short -> harmless no-op for the other pair

    context.forex_broker.get_candles.side_effect = get_candles_side_effect

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)  # must not raise

    assert context.forex_broker.get_candles.await_count == 2


@pytest.mark.asyncio
async def test_position_management_noop_when_forex_broker_not_configured():
    context = make_context()
    context.forex_broker = None
    await forex_position_management_cycle(context, MARKET_OPEN_TUESDAY)
    context.forex_position_repository.get_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_noop_when_market_closed():
    context = make_context()
    await forex_position_management_cycle(context, MARKET_CLOSED_SATURDAY)
    context.forex_position_repository.get_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_noop_when_no_tracked_positions():
    context = make_context()
    await forex_position_management_cycle(context, MARKET_OPEN_TUESDAY)
    context.forex_broker.get_open_trade_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_leaves_still_open_position_untouched():
    context = make_context()
    position = make_forex_position(pair="EUR_USD", oanda_trade_id="trade-1")
    context.forex_position_repository.get_all.return_value = [position]
    context.forex_broker.get_open_trade_ids.return_value = {"trade-1"}

    await forex_position_management_cycle(context, MARKET_OPEN_TUESDAY)

    context.trade_outcome_repository.record_outcome.assert_not_awaited()
    context.forex_position_repository.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_reconciles_closed_position():
    context = make_context()
    position = make_forex_position(pair="EUR_USD", oanda_trade_id="trade-1")
    context.forex_position_repository.get_all.return_value = [position]
    context.forex_broker.get_open_trade_ids.return_value = set()  # no longer open
    context.forex_broker.get_trade_realized_pnl.return_value = 42.5

    events = []
    await forex_position_management_cycle(context, MARKET_OPEN_TUESDAY, on_event=events.append)

    context.trade_outcome_repository.record_outcome.assert_awaited_once_with("EUR_USD", MARKET_OPEN_TUESDAY, 42.5)
    context.forex_position_repository.delete.assert_awaited_once_with("EUR_USD")
    context.alert_manager.send.assert_awaited_once()
    assert events[0]["type"] == "forex_position_closed"
    assert events[0]["pnl"] == 42.5


@pytest.mark.asyncio
async def test_position_management_continues_after_one_pair_raises():
    context = make_context()
    good = make_forex_position(pair="EUR_USD", oanda_trade_id="trade-1")
    bad = make_forex_position(pair="GBP_USD", oanda_trade_id="trade-2")
    context.forex_position_repository.get_all.return_value = [bad, good]
    context.forex_broker.get_open_trade_ids.return_value = set()

    async def get_pnl_side_effect(trade_id):
        if trade_id == "trade-2":
            raise RuntimeError("boom")
        return 10.0

    context.forex_broker.get_trade_realized_pnl.side_effect = get_pnl_side_effect

    await forex_position_management_cycle(context, MARKET_OPEN_TUESDAY)  # must not raise

    context.forex_position_repository.delete.assert_awaited_once_with("EUR_USD")


@pytest.mark.asyncio
async def test_progress_report_noop_when_notifier_not_configured():
    context = make_context()
    context.progress_notifier = None
    await forex_progress_report_cycle(context, MARKET_OPEN_TUESDAY)
    context.forex_broker.get_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_report_noop_when_forex_broker_not_configured():
    context = make_context()
    context.forex_broker = None
    await forex_progress_report_cycle(context, MARKET_OPEN_TUESDAY)
    context.progress_notifier.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_report_noop_when_market_closed():
    context = make_context()
    await forex_progress_report_cycle(context, MARKET_CLOSED_SATURDAY)
    context.progress_notifier.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_report_sends_status_message():
    context = make_context()
    context.forex_broker.get_account.return_value = make_account(equity=12345.67)
    context.forex_position_repository.get_all.return_value = [make_forex_position(pair="EUR_USD")]
    context.halt_manager.is_halted.return_value = False
    context.trade_outcome_repository.pnls_since.return_value = [50.0, -20.0]

    await forex_progress_report_cycle(context, MARKET_OPEN_TUESDAY)

    context.progress_notifier.send.assert_awaited_once()
    alert = context.progress_notifier.send.call_args.args[0]
    assert alert.title == "Forex progress"
    assert "12,345.67" in alert.message
    assert "open_positions=1" in alert.message
    assert "status=running" in alert.message


@pytest.mark.asyncio
async def test_progress_report_reports_halted_status():
    context = make_context()
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.halt_manager.is_halted.return_value = True

    await forex_progress_report_cycle(context, MARKET_OPEN_TUESDAY)

    alert = context.progress_notifier.send.call_args.args[0]
    assert "status=HALTED" in alert.message
