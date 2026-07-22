from datetime import datetime
from typing import TypedDict

import asyncpg

_INSERT_SQL = "INSERT INTO risk_halt_events (occurred_at, action, reason) VALUES ($1, $2, $3)"
_LATEST_SQL = "SELECT action, reason, occurred_at FROM risk_halt_events ORDER BY occurred_at DESC, id DESC LIMIT 1"


class HaltEvent(TypedDict):
    action: str
    reason: str
    occurred_at: datetime


class HaltRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_event(self, occurred_at: datetime, action: str, reason: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_INSERT_SQL, occurred_at, action, reason)

    async def latest_event(self) -> HaltEvent | None:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(_LATEST_SQL)
        return dict(record) if record else None
