from datetime import datetime

import asyncpg

_INSERT_SQL = """
INSERT INTO optionable_symbols_snapshot (computed_at, symbol)
VALUES ($1, $2)
ON CONFLICT (computed_at, symbol) DO NOTHING
"""

_LATEST_COMPUTED_AT_SQL = "SELECT MAX(computed_at) FROM optionable_symbols_snapshot"

_SYMBOLS_FOR_SNAPSHOT_SQL = """
SELECT symbol FROM optionable_symbols_snapshot WHERE computed_at = $1 ORDER BY symbol
"""


class OptionableSymbolsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save_snapshot(self, computed_at: datetime, symbols: list[str]) -> None:
        rows = [(computed_at, symbol) for symbol in symbols]
        async with self._pool.acquire() as conn:
            await conn.executemany(_INSERT_SQL, rows)

    async def latest_snapshot(self) -> tuple[datetime, list[str]] | None:
        async with self._pool.acquire() as conn:
            computed_at = await conn.fetchval(_LATEST_COMPUTED_AT_SQL)
            if computed_at is None:
                return None
            records = await conn.fetch(_SYMBOLS_FOR_SNAPSHOT_SQL, computed_at)
        return computed_at, [r["symbol"] for r in records]
