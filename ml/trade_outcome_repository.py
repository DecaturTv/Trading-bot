from datetime import datetime

import asyncpg

_INSERT_SQL = "INSERT INTO ml_trade_outcomes (symbol, closed_at, pnl) VALUES ($1, $2, $3)"

_RECENT_PNLS_SQL = "SELECT pnl FROM ml_trade_outcomes ORDER BY closed_at DESC, id DESC LIMIT $1"
_ALL_PNLS_SQL = "SELECT pnl FROM ml_trade_outcomes ORDER BY closed_at"


class TradeOutcomeRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_outcome(self, symbol: str, closed_at: datetime, pnl: float) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_INSERT_SQL, symbol, closed_at, pnl)

    async def recent_pnls(self, limit: int | None = None) -> list[float]:
        async with self._pool.acquire() as conn:
            if limit is None:
                records = await conn.fetch(_ALL_PNLS_SQL)
            else:
                records = await conn.fetch(_RECENT_PNLS_SQL, limit)
        return [r["pnl"] for r in records]
