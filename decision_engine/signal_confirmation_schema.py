import asyncpg

_SIGNAL_CONFIRMATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS signal_confirmation_state (
    symbol TEXT NOT NULL,
    vehicle TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    streak INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, vehicle, timeframe)
)
"""


async def apply_signal_confirmation_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SIGNAL_CONFIRMATION_TABLE_SQL)
