import asyncpg

# The "Breakout Hunter" parallel options strategy (dashboard/breakout_loop.py)
# keeps its open positions here, apart from trade_management_positions, so the
# two strategies can independently hold the same underlying. Same DDL as
# trade_management/position_state_schema.py — PositionStateRepository(table=
# "breakout_positions") reuses all its (de)serialization.
_BREAKOUT_POSITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS breakout_positions (
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


async def apply_breakout_position_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_BREAKOUT_POSITIONS_TABLE_SQL)
