import pytest
from broker.models import OrderSide, Position
from dash_factories import make_account, make_context

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
    context.broker.get_positions.return_value = [
        Position(symbol="PLUG", qty=135.0, side=OrderSide.BUY, avg_entry_price=2.32, market_value=291.6, unrealized_pl=-21.6),
        Position(symbol="RIG", qty=55.0, side=OrderSide.BUY, avg_entry_price=5.71, market_value=320.1, unrealized_pl=6.05),
    ]

    account = await get_effective_account(context)

    assert account.equity == pytest.approx(700.0 + 9.0 + (-21.6 + 6.05))
    context.trade_outcome_repository.recent_pnls.assert_awaited_once_with(asset_class="equities")


@pytest.mark.asyncio
async def test_live_mode_uses_real_broker_equity():
    context = make_context()
    context.settings.trading_mode = "live"
    context.settings.stock_account_start_balance = 500.0
    context.broker.get_account.return_value = make_account(equity=8234.56)

    account = await get_effective_account(context)

    assert account.equity == 8234.56
