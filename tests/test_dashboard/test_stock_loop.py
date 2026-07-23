from datetime import datetime, timezone

import pytest
from dash_factories import make_account, make_bars, make_context, make_position_record, make_stock_position_record

from broker.models import Order, OrderSide, OrderStatus, OrderType, Quote
from dashboard.stock_loop import stock_entry_cycle, stock_position_management_cycle
from decision_engine.models import FactorScore, TradeDirection, TradeSignal
from risk.kelly import KellyResult

MARKET_OPEN_TUESDAY = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)  # ~11am ET, a Tuesday
MARKET_CLOSED_SATURDAY = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)


def make_quote(symbol="AAPL", bid=99.5, ask=100.0):
    return Quote(symbol=symbol, bid_price=bid, ask_price=ask, bid_size=100.0, ask_size=100.0, timestamp=datetime.now(timezone.utc))


def make_order(order_id="order-1", symbol="AAPL", qty=10, side=OrderSide.BUY):
    return Order(
        order_id=order_id, symbol=symbol, qty=qty, side=side, order_type=OrderType.LIMIT, status=OrderStatus.NEW,
        filled_qty=0, filled_avg_price=None, submitted_at=None, filled_at=None,
    )


def bullish_signal(confidence=95.0):
    return TradeSignal(
        symbol="AAPL", direction=TradeDirection.BULLISH, confidence=confidence,
        factors=[FactorScore(name="momentum", value=0.9, weight=1.0)], meets_threshold=confidence >= 90,
    )


def bearish_signal(confidence=95.0):
    return TradeSignal(
        symbol="AAPL", direction=TradeDirection.BEARISH, confidence=confidence,
        factors=[FactorScore(name="momentum", value=-0.9, weight=1.0)], meets_threshold=confidence >= 90,
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
    await stock_entry_cycle(context, MARKET_CLOSED_SATURDAY)
    context.universe_manager.get_universe.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_noop_when_halted():
    context = make_context()
    context.halt_manager.is_halted.return_value = True
    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)
    context.universe_manager.get_universe.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_symbol_with_existing_options_position():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.position_repository.get.return_value = make_position_record(symbol="AAPL")

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.bars_repository.get_bars.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_symbol_with_existing_stock_position():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.stock_position_repository.get.return_value = make_stock_position_record(symbol="AAPL")

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.bars_repository.get_bars.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_insufficient_bars():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=5)

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.decision_model.score.assert_not_called()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_signal_does_not_meet_threshold():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = neutral_signal()

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.broker.get_latest_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_bearish_signal_long_only():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bearish_signal()

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.broker.get_latest_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_pre_trade_check_fails():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.broker.get_latest_quote.return_value = make_quote()
    context.pre_trade_checker.evaluate.return_value = _FailingCheck()

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.broker.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_skips_when_kelly_sizing_yields_zero_qty():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.broker.get_latest_quote.return_value = make_quote()
    context.pre_trade_checker.evaluate.return_value = _PassingCheck()
    context.kelly_sizer.size.return_value = KellyResult(full_kelly_fraction=0.0, position_fraction=0.0, used_fallback=True)

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.broker.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_cycle_happy_path_opens_position():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.decision_model.score.return_value = bullish_signal()
    context.broker.get_latest_quote.return_value = make_quote(ask=100.0)
    context.pre_trade_checker.evaluate.return_value = _PassingCheck()
    context.broker.get_account.return_value = make_account(equity=10000.0)
    context.kelly_sizer.size.return_value = KellyResult(full_kelly_fraction=0.1, position_fraction=0.1, used_fallback=True)
    context.broker.submit_order.return_value = make_order()

    events = []
    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY, on_event=events.append)

    context.broker.submit_order.assert_awaited_once()
    context.stock_position_repository.upsert.assert_awaited_once()
    context.alert_manager.send.assert_awaited_once()
    assert events[0]["type"] == "stock_position_opened"
    assert events[0]["symbol"] == "AAPL"
    assert events[0]["qty"] == 10  # budget=1000 (10% of 10000) // 100.0 ask


@pytest.mark.asyncio
async def test_entry_cycle_continues_after_one_symbol_raises():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL", "TSLA"]

    async def get_bars_side_effect(symbol, *args, **kwargs):
        if symbol == "AAPL":
            raise RuntimeError("boom")
        return make_bars(n=5)  # too short -> harmless no-op for the other symbol

    context.bars_repository.get_bars.side_effect = get_bars_side_effect

    await stock_entry_cycle(context, MARKET_OPEN_TUESDAY)  # must not raise

    assert context.bars_repository.get_bars.await_count == 2


@pytest.mark.asyncio
async def test_position_management_noop_when_market_closed():
    context = make_context()
    await stock_position_management_cycle(context, MARKET_CLOSED_SATURDAY)
    context.stock_position_repository.get_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_noop_when_no_exit_condition_met():
    context = make_context()
    record = make_stock_position_record(symbol="AAPL", qty=10, entry_cost=100.0)
    context.stock_position_repository.get_all.return_value = [record]
    context.broker.get_latest_quote.return_value = make_quote(bid=105.0)  # +5%, no rule triggered

    await stock_position_management_cycle(context, MARKET_OPEN_TUESDAY)

    context.broker.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_position_management_closes_full_position_on_stop_loss():
    context = make_context()
    record = make_stock_position_record(symbol="AAPL", qty=10, entry_cost=100.0)
    context.stock_position_repository.get_all.return_value = [record]
    context.broker.get_latest_quote.return_value = make_quote(bid=45.0)  # -55%, breaches -50% stop
    context.broker.submit_order.return_value = make_order(qty=10, side=OrderSide.SELL)

    events = []
    await stock_position_management_cycle(context, MARKET_OPEN_TUESDAY, on_event=events.append)

    context.broker.submit_order.assert_awaited_once()
    context.trade_outcome_repository.record_outcome.assert_awaited_once()
    context.stock_position_repository.delete.assert_awaited_once_with("AAPL")
    assert events[0]["type"] == "stock_position_closed"
    assert events[0]["action"] == "stop_loss"


@pytest.mark.asyncio
async def test_position_management_continues_after_one_symbol_raises():
    context = make_context()
    good = make_stock_position_record(symbol="AAPL", qty=10, entry_cost=100.0)
    bad = make_stock_position_record(symbol="TSLA", qty=5, entry_cost=200.0)
    context.stock_position_repository.get_all.return_value = [bad, good]

    async def get_quote_side_effect(symbol):
        if symbol == "TSLA":
            raise RuntimeError("boom")
        return make_quote(symbol=symbol, bid=45.0)  # AAPL: -55%, breaches stop

    context.broker.get_latest_quote.side_effect = get_quote_side_effect
    context.broker.submit_order.return_value = make_order(qty=10, side=OrderSide.SELL)

    await stock_position_management_cycle(context, MARKET_OPEN_TUESDAY)  # must not raise

    context.stock_position_repository.delete.assert_awaited_once_with("AAPL")
