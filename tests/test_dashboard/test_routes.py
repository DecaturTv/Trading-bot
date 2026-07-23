from dash_factories import make_account, make_context, make_forex_position, make_position_record, make_stock_position_record
from fastapi.testclient import TestClient

from config.settings import Settings
from dashboard.app import create_app

AUTH = {"Authorization": "Bearer test-token"}


def make_client(context):
    settings = Settings(_env_file=None, dashboard_auth_token="test-token", autonomous_trading_enabled=False)

    async def factory(_settings):
        return context

    app = create_app(settings=settings, context_factory=factory)
    return TestClient(app)


def test_requests_without_auth_are_rejected():
    with make_client(make_context()) as client:
        assert client.get("/api/account").status_code == 401


def test_get_account_returns_broker_account():
    context = make_context()
    context.broker.get_account.return_value = make_account(equity=1234.5)
    with make_client(context) as client:
        response = client.get("/api/account", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["equity"] == 1234.5


def test_get_halt_status():
    context = make_context()
    context.halt_manager.is_halted.return_value = True
    with make_client(context) as client:
        response = client.get("/api/halt", headers=AUTH)
    assert response.json() == {"equities": True, "forex": True}


def test_post_halt_without_scope_halts_both():
    context = make_context()
    with make_client(context) as client:
        response = client.post("/api/halt", headers=AUTH, json={"reason": "test"})
    assert response.status_code == 200
    assert response.json() == {"equities": True, "forex": True}
    assert context.halt_manager.halt.await_count == 2
    scopes_called = {call.args[2] for call in context.halt_manager.halt.call_args_list}
    assert scopes_called == {"equities", "forex"}
    assert context.halt_manager.halt.call_args_list[0].args[0] == "test"


def test_post_halt_with_scope_halts_only_that_scope():
    context = make_context()
    with make_client(context) as client:
        response = client.post("/api/halt", headers=AUTH, json={"reason": "test", "scope": "forex"})
    assert response.status_code == 200
    assert response.json() == {"forex": True}
    context.halt_manager.halt.assert_awaited_once()
    assert context.halt_manager.halt.call_args.args[2] == "forex"


def test_post_resume_without_scope_resumes_both():
    context = make_context()
    with make_client(context) as client:
        response = client.post("/api/resume", headers=AUTH, json={})
    assert response.status_code == 200
    assert response.json() == {"equities": False, "forex": False}
    assert context.halt_manager.resume.await_count == 2


def test_get_universe():
    context = make_context()
    context.universe_manager.get_universe.return_value = ["AAPL", "TSLA"]
    with make_client(context) as client:
        response = client.get("/api/universe", headers=AUTH)
    assert response.json() == {"symbols": ["AAPL", "TSLA"]}


def test_get_tracked_positions_serializes_nested_dataclasses():
    context = make_context()
    context.position_repository.get_all.return_value = [make_position_record(symbol="AAPL", qty=3)]
    with make_client(context) as client:
        response = client.get("/api/positions/tracked", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["state"]["qty"] == 3
    assert body[0]["legs"][0]["right"] == "call"


def test_get_stock_positions_returns_tracked_positions():
    context = make_context()
    context.stock_position_repository.get_all.return_value = [make_stock_position_record(symbol="AAPL", qty=10)]
    with make_client(context) as client:
        response = client.get("/api/stocks/positions", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["state"]["qty"] == 10


def test_get_forex_positions_returns_tracked_positions():
    context = make_context()
    context.forex_position_repository.get_all.return_value = [make_forex_position(pair="EUR_USD")]
    with make_client(context) as client:
        response = client.get("/api/forex/positions", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body[0]["pair"] == "EUR_USD"


def test_get_forex_positions_empty_when_forex_not_configured():
    context = make_context()
    context.forex_position_repository = None
    with make_client(context) as client:
        response = client.get("/api/forex/positions", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == []


def test_get_forex_account_returns_paper_equity_override():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.account_start_balance = 500.0
    context.forex_broker.get_account.return_value = make_account(equity=100000.0)
    with make_client(context) as client:
        response = client.get("/api/forex/account", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["equity"] == 500.0


def test_get_forex_account_null_when_forex_not_configured():
    context = make_context()
    context.forex_broker = None
    with make_client(context) as client:
        response = client.get("/api/forex/account", headers=AUTH)
    assert response.status_code == 200
    assert response.json() is None


def test_get_trade_outcomes_includes_statistics():
    context = make_context()
    context.trade_outcome_repository.recent_pnls.return_value = [150.0, 150.0, -100.0]
    with make_client(context) as client:
        response = client.get("/api/trade-outcomes", headers=AUTH)
    body = response.json()
    assert body["recent_pnls"] == [150.0, 150.0, -100.0]
    assert body["statistics"]["sample_size"] == 3


def test_static_index_served_at_root():
    with make_client(make_context()) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Trading Bot Dashboard" in response.text


def test_health_requires_auth_too():
    with make_client(make_context()) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/health", headers=AUTH).status_code == 200
