import asyncpg

# Positions held by the cross-sectional momentum book. Separate from
# forex_positions because the lifecycle is completely different: no
# stop-loss/take-profit, opened and closed only on the quarterly rebalance.
_XSMOM_POSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS forex_xsmom_positions (
    pair TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    units INTEGER NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    oanda_trade_id TEXT NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL
)
"""

# Single-row table (id is always 1) holding the last rebalance date, so the
# daily job knows when the next quarterly rebalance is due.
_XSMOM_STATE_SQL = """
CREATE TABLE IF NOT EXISTS forex_xsmom_state (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_rebalance_date DATE
)
"""

_SEED_STATE_SQL = "INSERT INTO forex_xsmom_state (id, last_rebalance_date) VALUES (1, NULL) ON CONFLICT (id) DO NOTHING"


async def apply_forex_xsmom_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_XSMOM_POSITIONS_SQL)
        await conn.execute(_XSMOM_STATE_SQL)
        await conn.execute(_SEED_STATE_SQL)
