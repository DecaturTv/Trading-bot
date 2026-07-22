import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

websocket_router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, event: dict) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)


@websocket_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)):
    # Browsers can't set Authorization headers on a WebSocket handshake, so
    # auth here is a query-param token instead of the REST routes' bearer header.
    settings = websocket.app.state.context.settings
    if not settings.dashboard_auth_token or token != settings.dashboard_auth_token:
        await websocket.close(code=1008)
        return

    manager: ConnectionManager = websocket.app.state.connection_manager
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # connection stays open; client->server messages are ignored
    except WebSocketDisconnect:
        manager.disconnect(websocket)
