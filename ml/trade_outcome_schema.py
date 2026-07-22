import asyncpg

_TRADE_OUTCOMES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ml_trade_outcomes (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    closed_at TIMESTAMPTZ NOT NULL,
    pnl DOUBLE PRECISION NOT NULL
)
"""


async def apply_trade_outcome_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_TRADE_OUTCOMES_TABLE_SQL)
