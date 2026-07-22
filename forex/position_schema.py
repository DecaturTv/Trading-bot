import asyncpg

_FOREX_POSITIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS forex_positions (
    pair TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    units INTEGER NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    stop_loss_price DOUBLE PRECISION NOT NULL,
    take_profit_price DOUBLE PRECISION NOT NULL,
    oanda_trade_id TEXT NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL
)
"""


async def apply_forex_position_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_FOREX_POSITIONS_TABLE_SQL)
