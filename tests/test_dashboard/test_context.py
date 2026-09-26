import pytest
from broker.models import OrderSide, Position
from dash_factories import make_account, make_context, make_position_record, make_stock_position_record

from dashboard.context import get_effective_account


@pytest.mark.asyncio
async def test_paper_mode_overrides_equity_with_stock_account_start_balance():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.stock_account_start_balance = 500.0
    context.broker.get_account.return_value = make_account(equity=100000.0, cash=100000.0, buying_power=400000.0)

    account = await get_effective_account(context)

    assert account.equity == 500.0
    assert account.cash == 100000.0
    assert account.buying_power == 400000.0


@pytest.mark.asyncio
async def test_paper_mode_equity_marks_to_market_realized_and_unrealized_pnl():
    """Regression test for equity being a flat constant that never moved as
    open positions gained/lost value -- see project memory on the
    stock-entries-blocked-by-exposure diagnosis: three positions worth more
    than the flat balance permanently tripped the exposure cap since the
    denominator could never grow to reflect them."""
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.stock_account_start_balance = 700.0
    context.broker.get_account.return_value = make_account(equity=100000.0)
    context.trade_outcome_repository.recent_pnls.return_value = [-6.0, 15.0]  # realized total +9.0
    # This sleeve actually holds both -- tracked in stock_position_repository.
    context.stock_position_repository.get_all.return_value = [
        make_stock_position_record(symbol="PLUG"), make_stock_position_record(symbol="RIG"),
    ]
    context.broker.get_positions.return_value = [
        Position(symbol="PLUG", qty=135.0, side=OrderSide.BUY, avg_entry_price=2.32, market_value=291.6, unrealized_pl=-21.6),
        Position(symbol="RIG", qty=55.0, side=OrderSide.BUY, avg_entry_price=5.71, market_value=320.1, unrealized_pl=6.05),
    ]

    account = await get_effective_account(context)

    assert account.equity == pytest.approx(700.0 + 9.0 + (-21.6 + 6.05))
    context.trade_outcome_repository.recent_pnls.assert_awaited_once_with(asset_class="equities")


@pytest.mark.asyncio
async def test_paper_mode_equity_ignores_unrealized_pnl_from_positions_this_sleeve_does_not_own():
    """The INTC incident: an 800-share position with a +$13k+ unrealized gain
    sat in the combined broker account untracked by any repository (a
    close-order bug let it ride into an option assignment -- see project
    memory). Before this fix, get_effective_account summed unrealized_pl
    across every broker.get_positions() entry, so that stray position's gain
    silently inflated the equities sleeve's equity even though nothing here
    opened it or manages it. Only positions this sleeve's own repositories
    actually track should move the number."""
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.stock_account_start_balance = 700.0
    context.broker.get_account.return_value = make_account(equity=100000.0)
    context.trade_outcome_repository.recent_pnls.return_value = []
    # Tracked: one option position on AAPL. Untracked: INTC, held at the
    # broker but not in stock_position_repository or position_repository.
    record = make_position_record(symbol="AAPL")
    leg_symbol = record.legs[0].symbol
    context.position_repository.get_all.return_value = [record]
    context.broker.get_positions.return_value = [
        Position(symbol=leg_symbol, qty=2.0, side=OrderSide.BUY, avg_entry_price=5.0, market_value=1200.0, unrealized_pl=200.0),
        Position(symbol="INTC", qty=800.0, side=OrderSide.BUY, avg_entry_price=106.13, market_value=98359.84, unrealized_pl=13455.84),
    ]

    account = await get_effective_account(context)

    assert account.equity == pytest.approx(700.0 + 200.0)


@pytest.mark.asyncio
async def test_live_mode_uses_real_broker_equity():
    context = make_context()
    context.settings.trading_mode = "live"
    context.settings.stock_account_start_balance = 500.0
    context.broker.get_account.return_value = make_account(equity=8234.56)

    account = await get_effective_account(context)

    assert account.equity == 8234.56
