import asyncpg

_STOCK_POSITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_positions (
    symbol TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    entry_date DATE NOT NULL,
    qty INTEGER NOT NULL,
    entry_cost_per_unit DOUBLE PRECISION NOT NULL,
    scaled_out BOOLEAN NOT NULL DEFAULT FALSE,
    peak_gain_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    updated_at TIMESTAMPTZ NOT NULL
)
"""


async def apply_stock_position_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_STOCK_POSITIONS_TABLE_SQL)
