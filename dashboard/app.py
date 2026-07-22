from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from config import get_settings
from config.settings import Settings

from .auth import require_auth
from .context import AppContext, build_context, close_context
from .routes import router
from .scheduler import build_scheduler
from .websocket import ConnectionManager, websocket_router

_STATIC_DIR = Path(__file__).parent / "static"

ContextFactory = Callable[[Settings], Awaitable[AppContext]]


def create_app(settings: Settings | None = None, context_factory: ContextFactory | None = None) -> FastAPI:
    """context_factory lets tests inject a prebuilt AppContext instead of the
    real build_context, which needs a live broker connection and Postgres."""
    settings = settings or get_settings()
    factory = context_factory or build_context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        context = await factory(settings)
        app.state.context = context
        app.state.connection_manager = ConnectionManager()

        scheduler = None
        if settings.autonomous_trading_enabled:
            scheduler = build_scheduler(context, on_event=app.state.connection_manager.broadcast)
            scheduler.start()
        app.state.scheduler = scheduler

        yield

        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await close_context(context)

    app = FastAPI(title="Trading Bot Dashboard", lifespan=lifespan)
    app.include_router(router, dependencies=[Depends(require_auth(settings))])
    app.include_router(websocket_router)
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
    return app
