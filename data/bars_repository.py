from datetime import datetime

import asyncpg

from broker.models import Bar

_UPSERT_SQL = """
INSERT INTO bars (symbol, timeframe, ts, open, high, low, close, volume)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (symbol, timeframe, ts) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume
"""

_SELECT_SQL = """
SELECT ts, open, high, low, close, volume
FROM bars
WHERE symbol = $1 AND timeframe = $2 AND ts BETWEEN $3 AND $4
ORDER BY ts
"""

_LATEST_SQL = "SELECT MAX(ts) FROM bars WHERE symbol = $1 AND timeframe = $2"


class BarsRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert_bars(self, timeframe: str, bars: list[Bar]) -> int:
        if not bars:
            return 0
        rows = [(b.symbol, timeframe, b.timestamp, b.open, b.high, b.low, b.close, b.volume) for b in bars]
        async with self._pool.acquire() as conn:
            await conn.executemany(_UPSERT_SQL, rows)
        return len(rows)

    async def get_bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(_SELECT_SQL, symbol, timeframe, start, end)
        return [
            Bar(
                symbol=symbol,
                timestamp=r["ts"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
            )
            for r in records
        ]

    async def latest_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(_LATEST_SQL, symbol, timeframe)
