from datetime import datetime

import asyncpg

from decision_engine.models import TradeDirection

from .sr_models import OpenSRStockPositionRecord, SRStockPositionState

_COLUMNS = "symbol, direction, entry_date, qty, entry_price, stop_price, target_price, stop_streak"

_UPSERT_SQL = """
INSERT INTO sr_stock_positions
    (symbol, direction, entry_date, qty, entry_price, stop_price, target_price, stop_streak, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (symbol) DO UPDATE SET
    direction = EXCLUDED.direction,
    entry_date = EXCLUDED.entry_date,
    qty = EXCLUDED.qty,
    entry_price = EXCLUDED.entry_price,
    stop_price = EXCLUDED.stop_price,
    target_price = EXCLUDED.target_price,
    stop_streak = EXCLUDED.stop_streak,
    updated_at = EXCLUDED.updated_at
"""

_GET_SQL = f"SELECT {_COLUMNS} FROM sr_stock_positions WHERE symbol = $1"
_GET_ALL_SQL = f"SELECT {_COLUMNS} FROM sr_stock_positions ORDER BY symbol"
_DELETE_SQL = "DELETE FROM sr_stock_positions WHERE symbol = $1"


def _row_to_record(row) -> OpenSRStockPositionRecord:
    return OpenSRStockPositionRecord(
        symbol=row["symbol"],
        direction=TradeDirection(row["direction"]),
        entry_date=row["entry_date"],
        state=SRStockPositionState(
            symbol=row["symbol"],
            qty=row["qty"],
            entry_price=row["entry_price"],
            stop_price=row["stop_price"],
            target_price=row["target_price"],
            stop_streak=row["stop_streak"],
        ),
    )


class SRStockPositionRepository:
    """One tracked S/R stock position per symbol, same shape as
    StockPositionRepository but in its own table (sr_stock_positions) so the
    two strategies' open positions don't collide."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(self, record: OpenSRStockPositionRecord, updated_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                record.symbol,
                record.direction.value,
                record.entry_date,
                record.state.qty,
                record.state.entry_price,
                record.state.stop_price,
                record.state.target_price,
                record.state.stop_streak,
                updated_at,
            )

    async def get(self, symbol: str) -> OpenSRStockPositionRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_GET_SQL, symbol)
        return _row_to_record(row) if row else None

    async def get_all(self) -> list[OpenSRStockPositionRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_GET_ALL_SQL)
        return [_row_to_record(r) for r in rows]

    async def delete(self, symbol: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SQL, symbol)
