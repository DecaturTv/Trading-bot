import asyncpg

_UNIVERSE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS universe_snapshots (
    computed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    rank INTEGER NOT NULL,
    PRIMARY KEY (computed_at, symbol)
)
"""


async def apply_universe_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UNIVERSE_TABLE_SQL)
