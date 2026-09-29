from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from dash_factories import make_context, make_forex_position, make_position_record, make_stock_position_record

from broker.models import OrderSide, OrderType, Position, PositionIntent
from decision_engine.models import TradeDirection
from forex.xsmom_repository import XsmomPosition
from dashboard.reconciliation_loop import Discrepancy, ReconciliationState, diff_positions, reconciliation_cycle

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


def _held(symbol, qty):
    return Position(symbol=symbol, qty=qty, side=OrderSide.BUY, avg_entry_price=1.0, market_value=1.0, unrealized_pl=0.0)


def _context(options=(), stocks=(), held=(), ignore=()):
    context = make_context()
    context.position_repository.get_all.return_value = list(options)
    context.breakout_position_repository.get_all.return_value = []
    context.sr_option_position_repository = AsyncMock()
    context.sr_option_position_repository.get_all.return_value = []
    context.stock_position_repository.get_all.return_value = list(stocks)
    context.sr_stock_position_repository = AsyncMock()
    context.sr_stock_position_repository.get_all.return_value = []
    context.broker.get_positions.return_value = list(held)
    context.settings.reconciliation_ignore_symbols = tuple(ignore)
    context.forex_broker = None  # equities-only unless a test wires forex in
    context.settings.reconciliation_auto_fix = False  # alert-only unless a test opts in
    return context


def _forex_context(technical=(), xsmom=(), open_trades=None, ignore=()):
    context = _context(ignore=ignore)
    context.forex_broker = AsyncMock()
    context.forex_broker.get_open_trades.return_value = open_trades or {}
    context.forex_position_repository.get_all.return_value = list(technical)
    context.forex_xsmom_repository = AsyncMock()
    context.forex_xsmom_repository.get_all.return_value = list(xsmom)
    return context


def _xsmom(pair, trade_id, units):
    return XsmomPosition(
        pair=pair, direction=TradeDirection.BULLISH, units=units, entry_price=1.1, score=0.5,
        oanda_trade_id=trade_id, opened_at=NOW,
    )


def test_diff_flags_both_directions_and_qty_mismatch():
    tracked = {"ETHA260925P00016500": 14, "AAPL": 10, "MSFT": 5}
    held = {"INTC": 800, "AAPL": 10, "MSFT": 3}

    kinds = {d.symbol: d.kind for d in diff_positions(tracked, held)}

    assert kinds == {"ETHA260925P00016500": "tracked_not_held", "INTC": "held_not_tracked", "MSFT": "qty_mismatch"}


def test_diff_compares_absolute_qty_so_short_legs_match():
    assert diff_positions({"X260101C00010000": 2}, {"X260101C00010000": -2}) == set()


def test_diff_skips_ignored_symbols():
    assert diff_positions({}, {"INTC": 800}, ignore=frozenset({"INTC"})) == set()


@pytest.mark.asyncio
async def test_alerts_only_after_two_consecutive_sightings():
    """The ETHA phantom: tracked in the options table, never held at Alpaca."""
    record = make_position_record(symbol="ETHA", qty=14, expiration=date(2026, 9, 25))
    context = _context(options=[record])
    state = ReconciliationState()

    await reconciliation_cycle(context, NOW, state)
    context.alert_manager.send.assert_not_awaited()  # could be a fill/close race

    await reconciliation_cycle(context, NOW, state)
    context.alert_manager.send.assert_awaited_once()
    alert = context.alert_manager.send.await_args.args[0]
    assert record.legs[0].symbol in alert.title
    assert "tracked_not_held" in alert.message


@pytest.mark.asyncio
async def test_transient_mismatch_that_clears_never_alerts():
    context = _context(held=[_held("AAPL", 10)])  # filled, not yet persisted
    state = ReconciliationState()

    await reconciliation_cycle(context, NOW, state)
    context.stock_position_repository.get_all.return_value = [make_stock_position_record(symbol="AAPL", qty=10)]
    await reconciliation_cycle(context, NOW, state)

    context.alert_manager.send.assert_not_awaited()
    assert state.previous == set()


@pytest.mark.asyncio
async def test_matching_book_and_ignored_stray_do_not_alert():
    record = make_stock_position_record(symbol="AAPL", qty=10)
    context = _context(stocks=[record], held=[_held("AAPL", 10), _held("INTC", 800)], ignore=["INTC"])
    state = ReconciliationState()

    await reconciliation_cycle(context, NOW, state)
    await reconciliation_cycle(context, NOW, state)

    context.alert_manager.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_untracked_stray_alerts():
    context = _context(held=[_held("INTC", 800)])
    state = ReconciliationState()

    await reconciliation_cycle(context, NOW, state)
    await reconciliation_cycle(context, NOW, state)

    alert = context.alert_manager.send.await_args.args[0]
    assert alert.dedup_key == "reconciliation-INTC-0-800"
    assert Discrepancy("INTC", 0, 800) in state.previous


async def _run_twice(context):
    state = ReconciliationState()
    await reconciliation_cycle(context, NOW, state)
    await reconciliation_cycle(context, NOW, state)
    return state


@pytest.mark.asyncio
async def test_forex_matching_trades_do_not_alert():
    technical = make_forex_position(pair="EUR_USD")
    context = _forex_context(
        technical=[technical], xsmom=[_xsmom("AUD_JPY", "t-9", 3000)],
        open_trades={technical.oanda_trade_id: ("EUR_USD", technical.units), "t-9": ("AUD_JPY", -3000)},
    )

    await _run_twice(context)

    context.alert_manager.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_forex_trade_open_at_oanda_but_untracked_alerts():
    """The xsmom rebalance drops tracking when a close fails -- the trade is
    still open at OANDA with nothing managing it."""
    context = _forex_context(open_trades={"t-7": ("GBP_USD", 5000)})

    await _run_twice(context)

    alert = context.alert_manager.send.await_args.args[0]
    assert alert.title == "Position mismatch (forex): GBP_USD:t-7"
    assert "held_not_tracked" in alert.message


@pytest.mark.asyncio
async def test_forex_tracked_trade_closed_at_oanda_alerts():
    context = _forex_context(xsmom=[_xsmom("NZD_USD", "t-3", 2000)])

    await _run_twice(context)

    assert "tracked_not_held" in context.alert_manager.send.await_args.args[0].message


@pytest.mark.asyncio
async def test_forex_ignore_list_matches_a_whole_pair():
    context = _forex_context(open_trades={"t-7": ("GBP_USD", 5000)}, ignore=["GBP_USD"])

    await _run_twice(context)

    context.alert_manager.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_broker_failing_does_not_blind_the_other():
    context = _forex_context(xsmom=[_xsmom("NZD_USD", "t-3", 2000)])
    context.broker.get_positions.side_effect = RuntimeError("alpaca down")

    await _run_twice(context)

    context.alert_manager.send.assert_awaited_once()  # the forex mismatch still alerts



# --- auto-fix -------------------------------------------------------------

CLOSED_SATURDAY = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_auto_fix_untracks_a_phantom_the_broker_confirms_it_does_not_hold():
    """ETHA/NVDL/ONDS/SOXS: tracked for four weeks, never filled, and blocking
    every sleeve's entries through the exposure check."""
    record = make_position_record(symbol="ETHA", qty=14, expiration=date(2026, 9, 25))
    context = _context(options=[record])
    context.settings.reconciliation_auto_fix = True
    context.broker.get_position.return_value = None

    await _run_twice(context)

    context.position_repository.delete.assert_awaited_once_with("ETHA")
    assert "Auto-fixed" in context.alert_manager.send.await_args.args[0].message


@pytest.mark.asyncio
async def test_auto_fix_keeps_tracking_if_the_direct_lookup_finds_the_position():
    record = make_position_record(symbol="ETHA", qty=14)
    context = _context(options=[record])
    context.settings.reconciliation_auto_fix = True
    context.broker.get_position.return_value = _held(record.legs[0].symbol, 14)  # bulk listing missed it

    await _run_twice(context)

    context.position_repository.delete.assert_not_awaited()
    assert "Not fixed" in context.alert_manager.send.await_args.args[0].message


@pytest.mark.asyncio
async def test_auto_fix_closes_an_untracked_stock_stray_at_market():
    context = _context(held=[_held("XYZ", 100)])
    context.settings.reconciliation_auto_fix = True
    context.broker.get_position.return_value = _held("XYZ", 100)

    await _run_twice(context)

    request = context.broker.submit_order.await_args.args[0]
    assert (request.symbol, request.qty, request.side, request.order_type) == ("XYZ", 100, OrderSide.SELL, OrderType.MARKET)
    assert request.position_intent is None
    assert "closed the untracked 100" in context.alert_manager.send.await_args.args[0].message


@pytest.mark.asyncio
async def test_auto_fix_tags_option_stray_closes_sell_to_close():
    """Without the tag Alpaca reads the sell as a naked short and rejects it."""
    symbol = "IBIT260925C00050000"
    context = _context(held=[_held(symbol, 8)])
    context.settings.reconciliation_auto_fix = True
    context.broker.get_position.return_value = _held(symbol, 8)

    await _run_twice(context)

    assert context.broker.submit_order.await_args.args[0].position_intent is PositionIntent.SELL_TO_CLOSE


@pytest.mark.asyncio
async def test_auto_fix_does_not_trade_strays_while_the_market_is_closed():
    context = _context(held=[_held("XYZ", 100)])
    context.settings.reconciliation_auto_fix = True
    context.broker.get_position.return_value = _held("XYZ", 100)
    state = ReconciliationState()

    await reconciliation_cycle(context, CLOSED_SATURDAY, state)
    await reconciliation_cycle(context, CLOSED_SATURDAY, state)

    context.broker.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_fix_never_touches_ignored_symbols():
    context = _context(held=[_held("INTC", 800)], ignore=["INTC"])
    context.settings.reconciliation_auto_fix = True

    await _run_twice(context)

    context.broker.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_fix_stands_down_when_drift_looks_systemic():
    """Four strays at once looks like a bad read, not four real strays."""
    held = [_held(s, 10) for s in ("AAA", "BBB", "CCC", "DDD")]
    context = _context(held=held)
    context.settings.reconciliation_auto_fix = True

    await _run_twice(context)

    context.broker.submit_order.assert_not_awaited()
    assert context.alert_manager.send.await_count == 4


@pytest.mark.asyncio
async def test_auto_fix_closes_an_untracked_forex_trade():
    context = _forex_context(open_trades={"t-7": ("GBP_USD", 5000)})
    context.settings.reconciliation_auto_fix = True
    context.forex_broker.get_open_trade_ids.return_value = {"t-7"}

    await _run_twice(context)

    context.forex_broker.close_trade.assert_awaited_once_with("t-7")


@pytest.mark.asyncio
async def test_auto_fix_leaves_forex_trades_oanda_closed_to_the_forex_loops():
    context = _forex_context(xsmom=[_xsmom("NZD_USD", "t-3", 2000)])
    context.settings.reconciliation_auto_fix = True

    await _run_twice(context)

    context.forex_xsmom_repository.delete.assert_not_awaited()
