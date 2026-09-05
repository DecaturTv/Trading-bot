import asyncpg

_SR_OPTION_POSITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sr_option_positions (
    symbol TEXT PRIMARY KEY,
    strategy_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_date DATE NOT NULL,
    legs JSONB NOT NULL,
    qty INTEGER NOT NULL,
    entry_cost_per_unit DOUBLE PRECISION NOT NULL,
    stop_price DOUBLE PRECISION NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    stop_streak INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL
)
"""


async def apply_sr_option_position_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SR_OPTION_POSITIONS_TABLE_SQL)
