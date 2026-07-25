from datetime import date, datetime

import asyncpg

from .models import CongressTrade, TransactionType

_UPSERT_SQL = """
INSERT INTO congress_trades
    (filing_id, ticker, transaction_date, transaction_type, representative, chamber, disclosure_date, amount_mid, source_url)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (filing_id, ticker, transaction_date, transaction_type) DO NOTHING
"""

_GET_RECENT_FOR_TICKER_SQL = """
SELECT filing_id, ticker, transaction_date, transaction_type, representative, chamber, disclosure_date, amount_mid, source_url
FROM congress_trades
WHERE ticker = $1 AND disclosure_date >= $2
ORDER BY disclosure_date DESC
"""

_LATEST_SYNCED_AT_SQL = "SELECT last_synced_at FROM congress_sync_state WHERE id = TRUE"
_RECORD_SYNCED_AT_SQL = """
INSERT INTO congress_sync_state (id, last_synced_at) VALUES (TRUE, $1)
ON CONFLICT (id) DO UPDATE SET last_synced_at = EXCLUDED.last_synced_at
"""


def _row_to_trade(row) -> CongressTrade:
    return CongressTrade(
        representative=row["representative"],
        chamber=row["chamber"],
        ticker=row["ticker"],
        transaction_type=TransactionType(row["transaction_type"]),
        transaction_date=row["transaction_date"],
        disclosure_date=row["disclosure_date"],
        amount_mid=row["amount_mid"],
        filing_id=row["filing_id"],
        source_url=row["source_url"],
    )


class CongressTradeRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert_many(self, trades: list[CongressTrade]) -> None:
        if not trades:
            return
        rows = [
            (
                t.filing_id, t.ticker, t.transaction_date, t.transaction_type.value,
                t.representative, t.chamber, t.disclosure_date, t.amount_mid, t.source_url,
            )
            for t in trades
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(_UPSERT_SQL, rows)

    async def get_recent_for_ticker(self, ticker: str, since: date) -> list[CongressTrade]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_GET_RECENT_FOR_TICKER_SQL, ticker, since)
        return [_row_to_trade(r) for r in rows]

    async def latest_synced_at(self) -> datetime | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_LATEST_SYNCED_AT_SQL)
        return row["last_synced_at"] if row else None

    async def record_synced_at(self, now: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_RECORD_SYNCED_AT_SQL, now)
