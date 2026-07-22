import pytest
from dash_factories import make_account, make_context

from dashboard.context import get_effective_account


@pytest.mark.asyncio
async def test_paper_mode_overrides_equity_with_account_start_balance():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.account_start_balance = 500.0
    context.broker.get_account.return_value = make_account(equity=100000.0, cash=100000.0, buying_power=400000.0)

    account = await get_effective_account(context)

    assert account.equity == 500.0
    assert account.cash == 100000.0
    assert account.buying_power == 400000.0


@pytest.mark.asyncio
async def test_live_mode_uses_real_broker_equity():
    context = make_context()
    context.settings.trading_mode = "live"
    context.settings.account_start_balance = 500.0
    context.broker.get_account.return_value = make_account(equity=8234.56)

    account = await get_effective_account(context)

    assert account.equity == 8234.56
