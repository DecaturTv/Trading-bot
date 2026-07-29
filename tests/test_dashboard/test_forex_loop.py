from datetime import datetime, timedelta, timezone

import pytest
from dash_factories import make_account, make_bars, make_context, make_forex_position

from broker.models import OrderSide
from dashboard.forex_loop import (
    forex_entry_cycle,
    forex_loss_limit_check_cycle,
    forex_position_management_cycle,
    forex_progress_report_cycle,
)
from decision_engine.models import FactorScore, TradeDirection, TradeSignal
from forex.oanda_adapter import TradeNotSettledError

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
async def test_entry_cycle_noop_when_entries_disabled():
    context = make_context()
    context.settings.forex_entries_enabled = False
    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)
    context.halt_manager.is_halted.assert_not_awaited()
    context.forex_broker.get_tradeable_pairs.assert_not_awaited()


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
async def test_entry_cycle_skips_pair_at_currency_concentration_cap():
    context = make_context()
    context.forex_broker.get_tradeable_pairs.return_value = ["EUR_ZAR"]
    context.forex_position_repository.get.return_value = None  # not already holding EUR_ZAR itself
    context.forex_position_repository.get_all.return_value = [
        make_forex_position(pair="CHF_ZAR"), make_forex_position(pair="GBP_ZAR"),
    ]

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.forex_broker.get_candles.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_insufficient_candles():
    context = make_context()
    context.forex_broker.get_candles.return_value = make_bars(n=5)

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.forex_decision_model.score.assert_not_called()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_signal_does_not_meet_threshold():
    context = make_context()
    context.forex_broker.get_candles.return_value = make_bars(n=40)
    context.forex_decision_model.score.return_value = neutral_signal()

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.forex_broker.get_pricing.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_happy_path_opens_position():
    context = make_context()
    context.forex_broker.get_candles.return_value = make_bars(n=40)
    context.forex_decision_model.score.return_value = bullish_signal()
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

    context.feature_store_repository.record_snapshot.assert_awaited_once()
    snapshot_call = context.feature_store_repository.record_snapshot.call_args
    assert snapshot_call.args[0] == "EUR_USD"
    assert snapshot_call.args[2] == {"momentum": 0.9}
    assert snapshot_call.args[3] == 95.0
    assert snapshot_call.args[4] == "bullish"
    persisted_position = context.forex_position_repository.upsert.call_args.args[0]
    assert persisted_position.feature_snapshot_id == 1


@pytest.mark.asyncio
async def test_entry_cycle_converts_stop_distance_for_non_usd_quoted_pair():
    context = make_context()
    # USD_JPY is only in the universe to serve as the conversion pair for
    # sizing EUR_JPY -- give it an existing position so it's skipped as its
    # own entry candidate rather than also opening a trade.
    context.forex_broker.get_tradeable_pairs.return_value = ["EUR_JPY", "USD_JPY"]
    context.forex_position_repository.get.side_effect = (
        lambda pair: make_forex_position(pair="USD_JPY") if pair == "USD_JPY" else None
    )
    context.forex_broker.get_candles.return_value = make_bars(n=40)
    context.forex_decision_model.score.return_value = TradeSignal(
        symbol="EUR_JPY", direction=TradeDirection.BULLISH, confidence=95.0,
        factors=[FactorScore(name="momentum", value=0.9, weight=1.0)], meets_threshold=True,
    )

    async def get_pricing_side_effect(pair):
        if pair == "USD_JPY":
            return (110.0, 110.2)  # conversion pair
        return (163.0, 163.02)  # EUR_JPY entry price

    context.forex_broker.get_pricing.side_effect = get_pricing_side_effect
    context.forex_broker.submit_market_order.return_value = "trade-1"
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.forex_broker.submit_market_order.assert_awaited_once()
    call = context.forex_broker.submit_market_order.call_args
    assert call.args[0] == "EUR_JPY"
    # Without the JPY/USD conversion, stop_distance (in JPY) would be treated
    # as USD directly, sizing to a handful of units. With conversion applied
    # (~110 JPY per USD), the account-currency risk per unit shrinks, so the
    # position should come out far larger than that unconverted case.
    assert call.args[1] > 1000


@pytest.mark.asyncio
async def test_entry_cycle_skips_pair_with_no_tradeable_conversion_pair():
    context = make_context()
    context.forex_broker.get_tradeable_pairs.return_value = ["EUR_JPY"]  # no JPY_USD/USD_JPY available
    context.forex_broker.get_candles.return_value = make_bars(n=40)
    context.forex_decision_model.score.return_value = TradeSignal(
        symbol="EUR_JPY", direction=TradeDirection.BULLISH, confidence=95.0,
        factors=[FactorScore(name="momentum", value=0.9, weight=1.0)], meets_threshold=True,
    )
    context.forex_broker.get_pricing.return_value = (163.0, 163.02)
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)

    await forex_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.forex_broker.submit_market_order.assert_not_awaited()


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

    context.trade_outcome_repository.record_outcome.assert_awaited_once()
    call = context.trade_outcome_repository.record_outcome.call_args
    assert call.args == ("EUR_USD", MARKET_OPEN_TUESDAY, 42.5)
    assert call.kwargs["asset_class"] == "forex"
    details = call.kwargs["details"]
    assert details["side"] == "buy"
    assert details["units"] == 1000
    assert details["entry_price"] == 1.1000
    assert details["stop_loss_price"] == 1.0950
    assert details["take_profit_price"] == 1.1100
    context.forex_position_repository.delete.assert_awaited_once_with("EUR_USD")
    context.alert_manager.send.assert_awaited_once()
    assert events[0]["type"] == "forex_position_closed"
    assert events[0]["pnl"] == 42.5
    # No feature_snapshot_id on this position (default) -- nothing to label.
    context.feature_store_repository.record_outcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_labels_feature_snapshot_on_reconcile():
    context = make_context()
    position = make_forex_position(pair="EUR_USD", oanda_trade_id="trade-1", feature_snapshot_id=7)
    context.forex_position_repository.get_all.return_value = [position]
    context.forex_broker.get_open_trade_ids.return_value = set()
    context.forex_broker.get_trade_realized_pnl.return_value = -12.5

    await forex_position_management_cycle(context, MARKET_OPEN_TUESDAY)

    context.feature_store_repository.record_outcome.assert_awaited_once_with(7, -12.5)


@pytest.mark.asyncio
async def test_position_management_leaves_position_tracked_when_pnl_not_yet_settled():
    context = make_context()
    position = make_forex_position(pair="EUR_USD", oanda_trade_id="trade-1")
    context.forex_position_repository.get_all.return_value = [position]
    context.forex_broker.get_open_trade_ids.return_value = set()  # no longer open
    context.forex_broker.get_trade_realized_pnl.side_effect = TradeNotSettledError("trade-1")

    await forex_position_management_cycle(context, MARKET_OPEN_TUESDAY)

    context.trade_outcome_repository.record_outcome.assert_not_awaited()
    context.forex_position_repository.delete.assert_not_awaited()
    context.alert_manager.send.assert_not_awaited()


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
async def test_progress_report_lists_open_and_closed_positions():
    context = make_context()
    context.forex_broker.get_account.return_value = make_account(equity=500.0)
    context.forex_position_repository.get_all.return_value = [
        make_forex_position(pair="NZD_HKD", side=OrderSide.SELL, units=700, entry_price=4.54399)
    ]
    context.trade_outcome_repository.recent_trades.return_value = [
        {"symbol": "USD_CHF", "closed_at": MARKET_OPEN_TUESDAY, "pnl": -12.3853, "asset_class": "forex", "details": {}},
        {
            "symbol": "OLD_PAIR",
            "closed_at": MARKET_OPEN_TUESDAY - timedelta(days=1),
            "pnl": 5.0,
            "asset_class": "forex",
            "details": {},
        },
    ]

    await forex_progress_report_cycle(context, MARKET_OPEN_TUESDAY)

    alert = context.progress_notifier.send.call_args.args[0]
    assert "Open positions:" in alert.message
    assert "NZD_HKD sell units=700 entry=4.54399" in alert.message
    assert "Closed today:" in alert.message
    assert "USD_CHF pnl=-12.39" in alert.message
    assert "OLD_PAIR" not in alert.message  # closed yesterday, excluded


@pytest.mark.asyncio
async def test_progress_report_omits_sections_when_nothing_to_show():
    context = make_context()
    context.forex_broker.get_account.return_value = make_account(equity=500.0)

    await forex_progress_report_cycle(context, MARKET_OPEN_TUESDAY)

    alert = context.progress_notifier.send.call_args.args[0]
    assert "Open positions:" not in alert.message
    assert "Closed today:" not in alert.message


@pytest.mark.asyncio
async def test_progress_report_reports_halted_status():
    context = make_context()
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.halt_manager.is_halted.return_value = True

    await forex_progress_report_cycle(context, MARKET_OPEN_TUESDAY)

    alert = context.progress_notifier.send.call_args.args[0]
    assert "status=HALTED" in alert.message


@pytest.mark.asyncio
async def test_loss_limit_check_noop_when_forex_broker_not_configured():
    context = make_context()
    context.forex_broker = None
    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)
    context.halt_manager.is_halted.assert_not_awaited()


@pytest.mark.asyncio
async def test_loss_limit_check_noop_when_already_halted():
    context = make_context()
    context.halt_manager.is_halted.return_value = True
    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)
    context.forex_broker.get_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_loss_limit_check_scopes_halt_and_pnls_to_forex():
    context = make_context()
    context.settings.trading_mode = "live"
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.trade_outcome_repository.pnls_since.return_value = [-10.0]
    context.halt_manager.check_and_halt_on_loss_limits.return_value = False

    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.halt_manager.is_halted.assert_awaited_once_with("forex")
    for call in context.trade_outcome_repository.pnls_since.call_args_list:
        assert call.kwargs["asset_class"] == "forex"
    assert context.halt_manager.check_and_halt_on_loss_limits.call_args.kwargs["scope"] == "forex"


@pytest.mark.asyncio
async def test_loss_limit_check_skips_weekly_window_in_paper_mode():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.account_start_balance = 500.0
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.trade_outcome_repository.pnls_since.return_value = [-1000.0]

    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.trade_outcome_repository.pnls_since.assert_awaited_once()  # daily only, not daily+weekly


@pytest.mark.asyncio
async def test_loss_limit_check_does_not_halt_within_limits():
    context = make_context()
    context.settings.trading_mode = "live"
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.trade_outcome_repository.pnls_since.return_value = [-10.0]
    context.halt_manager.check_and_halt_on_loss_limits.return_value = False

    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.alert_manager.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_loss_limit_check_sends_critical_alert_when_triggered():
    context = make_context()
    context.settings.trading_mode = "live"
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.trade_outcome_repository.pnls_since.return_value = [-1000.0]
    context.halt_manager.check_and_halt_on_loss_limits.return_value = True

    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.alert_manager.send.assert_awaited_once()
    alert = context.alert_manager.send.call_args.args[0]
    assert alert.severity.value == "critical"
    assert "forex" in alert.title.lower()
    assert "halted" in alert.title.lower()


@pytest.mark.asyncio
async def test_loss_limit_check_paper_mode_never_halts():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.account_start_balance = 500.0
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.trade_outcome_repository.pnls_since.return_value = [-1000.0]

    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.halt_manager.halt.assert_not_awaited()
    context.halt_manager.check_and_halt_on_loss_limits.assert_not_awaited()


@pytest.mark.asyncio
async def test_loss_limit_check_paper_mode_notifies_on_breach_without_halting():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.account_start_balance = 500.0
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.trade_outcome_repository.pnls_since.return_value = [-1000.0]  # -200% of $500, breaches 5% daily limit

    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.alert_manager.send.assert_awaited_once()
    alert = context.alert_manager.send.call_args.args[0]
    assert alert.severity.value == "warning"
    assert "forex" in alert.title.lower()
    assert "not halted" in alert.title.lower()
    assert "daily loss limit breached" in alert.message


@pytest.mark.asyncio
async def test_loss_limit_check_paper_mode_no_alert_within_limits():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.account_start_balance = 500.0
    context.forex_broker.get_account.return_value = make_account(equity=10000.0)
    context.trade_outcome_repository.pnls_since.return_value = [-10.0]  # -2%, within the 5% limit

    await forex_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.alert_manager.send.assert_not_awaited()
