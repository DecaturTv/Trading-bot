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
    updated_at TIMESTAMPTZ NOT NULL
)
"""


async def apply_position_state_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_POSITION_STATE_TABLE_SQL)
