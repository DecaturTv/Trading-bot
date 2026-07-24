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

# Freeform per-trade context (entry/exit price, stop/target, direction,
# confidence, exit reason -- whatever the caller has) that used to be
# discarded once a position closed, making real loss post-mortems
# impossible (only symbol/pnl/timestamp survived). jsonb rather than more
# typed columns since the useful fields differ by asset_class (a forex
# trade has stop/target prices; an options trade has strike/expiration/DTE).
_ADD_DETAILS_COLUMN_SQL = """
ALTER TABLE ml_trade_outcomes ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb
"""


async def apply_trade_outcome_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_TRADE_OUTCOMES_TABLE_SQL)
        await conn.execute(_ADD_ASSET_CLASS_COLUMN_SQL)
        await conn.execute(_BACKFILL_FOREX_ASSET_CLASS_SQL)
        await conn.execute(_ADD_DETAILS_COLUMN_SQL)
