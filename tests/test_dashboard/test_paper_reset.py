from datetime import datetime, timezone

import pytest
from dash_factories import make_context

from dashboard.paper_reset import paper_trading_daily_reset_cycle

NOW = datetime(2026, 7, 24, 4, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_noop_in_live_mode():
    context = make_context()
    context.settings.trading_mode = "live"

    await paper_trading_daily_reset_cycle(context, NOW)

    context.halt_manager.resume.assert_not_awaited()
    context.alert_manager.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_resumes_both_scopes_in_paper_mode():
    context = make_context()
    context.settings.trading_mode = "paper"

    await paper_trading_daily_reset_cycle(context, NOW)

    assert context.halt_manager.resume.await_count == 2
    scopes = {call.args[2] for call in context.halt_manager.resume.call_args_list}
    assert scopes == {"equities", "forex"}
    context.alert_manager.send.assert_awaited_once()
