import asyncpg

_CONGRESS_TRADES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS congress_trades (
    filing_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    transaction_date DATE NOT NULL,
    transaction_type TEXT NOT NULL,
    representative TEXT NOT NULL,
    chamber TEXT NOT NULL,
    disclosure_date DATE NOT NULL,
    amount_mid DOUBLE PRECISION NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY (filing_id, ticker, transaction_date, transaction_type)
)
"""

_CONGRESS_TRADES_TICKER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS congress_trades_ticker_idx ON congress_trades (ticker, disclosure_date)
"""

# Single-row table tracking when the free source was last fetched, so
# CongressTradeManager can rate-limit refreshes the same way UniverseManager
# does for the active/optionable symbol snapshots.
_CONGRESS_SYNC_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS congress_sync_state (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    last_synced_at TIMESTAMPTZ NOT NULL
)
"""


async def apply_congress_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CONGRESS_TRADES_TABLE_SQL)
        await conn.execute(_CONGRESS_TRADES_TICKER_INDEX_SQL)
        await conn.execute(_CONGRESS_SYNC_STATE_TABLE_SQL)
