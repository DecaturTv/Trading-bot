import pytest
from dash_factories import make_context
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from config.settings import Settings
from dashboard.app import create_app
from dashboard.websocket import ConnectionManager


def make_client(context):
    settings = Settings(_env_file=None, dashboard_auth_token="test-token", autonomous_trading_enabled=False)

    async def factory(_settings):
        return context

    app = create_app(settings=settings, context_factory=factory)
    return TestClient(app)


def test_websocket_rejects_missing_token():
    with make_client(make_context()) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 1008


def test_websocket_rejects_wrong_token():
    with make_client(make_context()) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?token=wrong"):
                pass
        assert exc_info.value.code == 1008


def test_websocket_accepts_correct_token():
    with make_client(make_context()) as client:
        with client.websocket_connect("/ws?token=test-token"):
            pass  # connecting without being immediately closed is the assertion


@pytest.mark.asyncio
async def test_connection_manager_broadcasts_to_all_connections():
    manager = ConnectionManager()
    ws1, ws2 = _FakeWebSocket(), _FakeWebSocket()
    manager._connections.add(ws1)
    manager._connections.add(ws2)

    await manager.broadcast({"type": "position_opened", "symbol": "AAPL"})

    assert ws1.sent == [{"type": "position_opened", "symbol": "AAPL"}]
    assert ws2.sent == [{"type": "position_opened", "symbol": "AAPL"}]


@pytest.mark.asyncio
async def test_connection_manager_drops_dead_connections_without_failing_others():
    manager = ConnectionManager()
    dead, alive = _FakeWebSocket(fail=True), _FakeWebSocket()
    manager._connections.add(dead)
    manager._connections.add(alive)

    await manager.broadcast({"type": "ping"})

    assert alive.sent == [{"type": "ping"}]
    assert dead not in manager._connections


class _FakeWebSocket:
    def __init__(self, fail=False):
        self.sent = []
        self._fail = fail

    async def send_json(self, data):
        if self._fail:
            raise RuntimeError("connection closed")
        self.sent.append(data)
