import asyncpg

_POSITION_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trade_management_positions (
    symbol TEXT PRIMARY KEY,
    strategy_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_date DATE NOT NULL,
    legs JSONB NOT NULL,
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
ALTER TABLE trade_management_positions ADD COLUMN IF NOT EXISTS stop_loss_streak INTEGER NOT NULL DEFAULT 0
"""

_ADD_REVERSAL_STREAK_COLUMN_SQL = """
ALTER TABLE trade_management_positions ADD COLUMN IF NOT EXISTS reversal_streak INTEGER NOT NULL DEFAULT 0
"""

_ADD_TRAILING_STOP_STREAK_COLUMN_SQL = """
ALTER TABLE trade_management_positions ADD COLUMN IF NOT EXISTS trailing_stop_streak INTEGER NOT NULL DEFAULT 0
"""


# Underlying-price stop, 2026-09-29 (see PositionState). Shared with
# breakout_positions, which has the same DDL.
UNDERLYING_STOP_COLUMNS_SQL = """
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS entry_underlying_price DOUBLE PRECISION;
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS underlying_stop_price DOUBLE PRECISION;
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS catastrophic_streak INTEGER NOT NULL DEFAULT 0;
"""


async def apply_position_state_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_POSITION_STATE_TABLE_SQL)
        await conn.execute(_ADD_STOP_LOSS_STREAK_COLUMN_SQL)
        await conn.execute(_ADD_REVERSAL_STREAK_COLUMN_SQL)
        await conn.execute(_ADD_TRAILING_STOP_STREAK_COLUMN_SQL)
        await conn.execute(UNDERLYING_STOP_COLUMNS_SQL.format(table="trade_management_positions"))
