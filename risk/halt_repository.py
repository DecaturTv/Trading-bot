from datetime import datetime
from typing import TypedDict

import asyncpg

_INSERT_SQL = "INSERT INTO risk_halt_events (occurred_at, action, reason, scope) VALUES ($1, $2, $3, $4)"
_LATEST_SQL = (
    "SELECT action, reason, occurred_at FROM risk_halt_events WHERE scope = $1 ORDER BY occurred_at DESC, id DESC LIMIT 1"
)


class HaltEvent(TypedDict):
    action: str
    reason: str
    occurred_at: datetime


class HaltRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_event(self, occurred_at: datetime, action: str, reason: str, scope: str = "equities") -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_INSERT_SQL, occurred_at, action, reason, scope)

    async def latest_event(self, scope: str = "equities") -> HaltEvent | None:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(_LATEST_SQL, scope)
        return dict(record) if record else None
