from dataclasses import dataclass
from datetime import datetime

import asyncpg

from .models import TradeDirection

_UPSERT_SQL = """
INSERT INTO signal_confirmation_state (symbol, vehicle, timeframe, direction, streak, updated_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (symbol, vehicle, timeframe) DO UPDATE SET
    direction = EXCLUDED.direction,
    streak = EXCLUDED.streak,
    updated_at = EXCLUDED.updated_at
"""

_GET_SQL = "SELECT direction, streak, updated_at FROM signal_confirmation_state WHERE symbol = $1 AND vehicle = $2 AND timeframe = $3"
_DELETE_SQL = "DELETE FROM signal_confirmation_state WHERE symbol = $1 AND vehicle = $2 AND timeframe = $3"


@dataclass(frozen=True)
class SignalConfirmationState:
    direction: TradeDirection
    streak: int
    updated_at: datetime


class SignalConfirmationRepository:
    """Persists the consecutive-scan streak an entry signal has built up per
    (symbol, vehicle, timeframe) — vehicle distinguishes the stock and
    options entry cycles (which can independently hold a position in the
    same symbol), timeframe distinguishes the options cycle's concurrent
    5m/15m/1h/1d scans, matching how entries themselves are gated in
    dashboard/stock_loop.py and dashboard/trading_loop.py.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get(self, symbol: str, vehicle: str, timeframe: str) -> SignalConfirmationState | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_GET_SQL, symbol, vehicle, timeframe)
        if row is None:
            return None
        return SignalConfirmationState(direction=TradeDirection(row["direction"]), streak=row["streak"], updated_at=row["updated_at"])

    async def upsert(self, symbol: str, vehicle: str, timeframe: str, direction: TradeDirection, streak: int, updated_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, symbol, vehicle, timeframe, direction.value, streak, updated_at)

    async def clear(self, symbol: str, vehicle: str, timeframe: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SQL, symbol, vehicle, timeframe)
