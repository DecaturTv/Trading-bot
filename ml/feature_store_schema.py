import asyncpg

_FEATURE_SNAPSHOTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ml_feature_snapshots (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    factors JSONB NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    direction TEXT NOT NULL,
    pnl DOUBLE PRECISION,
    win BOOLEAN
)
"""


async def apply_feature_store_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_FEATURE_SNAPSHOTS_TABLE_SQL)
