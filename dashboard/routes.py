from datetime import datetime, timezone

from fastapi import APIRouter, Request

from risk.statistics import compute_trade_statistics

from .context import AppContext

router = APIRouter(prefix="/api")


def _context(request: Request) -> AppContext:
    return request.app.state.context


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/account")
async def get_account(request: Request):
    return await _context(request).broker.get_account()


@router.get("/positions")
async def get_positions(request: Request):
    return await _context(request).broker.get_positions()


@router.get("/positions/tracked")
async def get_tracked_positions(request: Request):
    return await _context(request).position_repository.get_all()


@router.get("/halt")
async def get_halt_status(request: Request):
    halted = await _context(request).halt_manager.is_halted()
    return {"halted": halted}


@router.post("/halt")
async def halt_trading(request: Request, body: dict):
    context = _context(request)
    reason = body.get("reason", "manual halt via dashboard")
    await context.halt_manager.halt(reason, datetime.now(timezone.utc))
    return {"halted": True}


@router.post("/resume")
async def resume_trading(request: Request, body: dict):
    context = _context(request)
    reason = body.get("reason", "manual resume via dashboard")
    await context.halt_manager.resume(reason, datetime.now(timezone.utc))
    return {"halted": False}


@router.get("/universe")
async def get_universe(request: Request):
    context = _context(request)
    symbols = await context.universe_manager.get_universe(datetime.now(timezone.utc))
    return {"symbols": symbols}


@router.get("/trade-outcomes")
async def get_trade_outcomes(request: Request, limit: int = 50):
    context = _context(request)
    pnls = await context.trade_outcome_repository.recent_pnls(limit=limit)
    return {"recent_pnls": pnls, "statistics": compute_trade_statistics(pnls)}
