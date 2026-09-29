from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from dash_factories import make_context, make_position_record, make_stock_position_record

from broker.models import OrderSide, Position
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
    return context


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
