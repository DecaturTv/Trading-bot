import asyncpg

_OPTIONABLE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS optionable_symbols_snapshot (
    computed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    PRIMARY KEY (computed_at, symbol)
)
"""


async def apply_optionable_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_OPTIONABLE_TABLE_SQL)
