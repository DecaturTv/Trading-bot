from datetime import datetime

import asyncpg

from broker.models import ActiveSymbol

_INSERT_SQL = """
INSERT INTO universe_snapshots (computed_at, symbol, volume, rank)
VALUES ($1, $2, $3, $4)
ON CONFLICT (computed_at, symbol) DO NOTHING
"""

_LATEST_COMPUTED_AT_SQL = "SELECT MAX(computed_at) FROM universe_snapshots"

_SYMBOLS_FOR_SNAPSHOT_SQL = """
SELECT symbol FROM universe_snapshots WHERE computed_at = $1 ORDER BY rank
"""


class UniverseRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save_snapshot(self, computed_at: datetime, symbols: list[ActiveSymbol]) -> None:
        rows = [(computed_at, s.symbol, s.volume, rank) for rank, s in enumerate(symbols, start=1)]
        async with self._pool.acquire() as conn:
            await conn.executemany(_INSERT_SQL, rows)

    async def latest_snapshot(self) -> tuple[datetime, list[str]] | None:
        async with self._pool.acquire() as conn:
            computed_at = await conn.fetchval(_LATEST_COMPUTED_AT_SQL)
            if computed_at is None:
                return None
            records = await conn.fetch(_SYMBOLS_FOR_SNAPSHOT_SQL, computed_at)
        return computed_at, [r["symbol"] for r in records]
