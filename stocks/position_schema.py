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
    stop_loss_streak INTEGER NOT NULL DEFAULT 0,
    reversal_streak INTEGER NOT NULL DEFAULT 0,
    trailing_stop_streak INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

_ADD_STOP_LOSS_STREAK_COLUMN_SQL = """
ALTER TABLE stock_positions ADD COLUMN IF NOT EXISTS stop_loss_streak INTEGER NOT NULL DEFAULT 0
"""

_ADD_REVERSAL_STREAK_COLUMN_SQL = """
ALTER TABLE stock_positions ADD COLUMN IF NOT EXISTS reversal_streak INTEGER NOT NULL DEFAULT 0
"""

_ADD_TRAILING_STOP_STREAK_COLUMN_SQL = """
ALTER TABLE stock_positions ADD COLUMN IF NOT EXISTS trailing_stop_streak INTEGER NOT NULL DEFAULT 0
"""


async def apply_stock_position_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_STOCK_POSITIONS_TABLE_SQL)
        await conn.execute(_ADD_STOP_LOSS_STREAK_COLUMN_SQL)
        await conn.execute(_ADD_REVERSAL_STREAK_COLUMN_SQL)
        await conn.execute(_ADD_TRAILING_STOP_STREAK_COLUMN_SQL)
