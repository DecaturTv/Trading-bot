import asyncpg

_TRADE_OUTCOMES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ml_trade_outcomes (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    closed_at TIMESTAMPTZ NOT NULL,
    pnl DOUBLE PRECISION NOT NULL
)
"""

_ADD_ASSET_CLASS_COLUMN_SQL = """
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS asset_class TEXT NOT NULL DEFAULT 'equities'
"""

# One-time backfill for rows written before asset_class existed: OANDA pairs
# are always THREE_THREE (e.g. EUR_USD, USD_JPY) -- equities/options symbols
# never match that shape. Safe to re-run: already-labeled 'forex' rows no
# longer match asset_class = 'equities' so this becomes a no-op after the
# first pass.
_BACKFILL_FOREX_ASSET_CLASS_SQL = r"""
UPDATE ml_trade_outcomes SET asset_class = 'forex'
WHERE asset_class = 'equities' AND symbol ~ '^[A-Z]{3}_[A-Z]{3}$'
"""


async def apply_trade_outcome_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_TRADE_OUTCOMES_TABLE_SQL)
        await conn.execute(_ADD_ASSET_CLASS_COLUMN_SQL)
        await conn.execute(_BACKFILL_FOREX_ASSET_CLASS_SQL)
