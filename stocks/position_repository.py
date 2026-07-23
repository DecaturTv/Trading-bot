from datetime import datetime

import asyncpg

from decision_engine.models import TradeDirection
from trade_management.models import PositionState

from .models import OpenStockPositionRecord

_COLUMNS = "symbol, direction, entry_date, qty, entry_cost_per_unit, scaled_out, peak_gain_pct"

_UPSERT_SQL = """
INSERT INTO stock_positions (symbol, direction, entry_date, qty, entry_cost_per_unit, scaled_out, peak_gain_pct, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (symbol) DO UPDATE SET
    direction = EXCLUDED.direction,
    entry_date = EXCLUDED.entry_date,
    qty = EXCLUDED.qty,
    entry_cost_per_unit = EXCLUDED.entry_cost_per_unit,
    scaled_out = EXCLUDED.scaled_out,
    peak_gain_pct = EXCLUDED.peak_gain_pct,
    updated_at = EXCLUDED.updated_at
"""

_GET_SQL = f"SELECT {_COLUMNS} FROM stock_positions WHERE symbol = $1"
_GET_ALL_SQL = f"SELECT {_COLUMNS} FROM stock_positions ORDER BY symbol"
_DELETE_SQL = "DELETE FROM stock_positions WHERE symbol = $1"


def _row_to_record(row) -> OpenStockPositionRecord:
    return OpenStockPositionRecord(
        symbol=row["symbol"],
        direction=TradeDirection(row["direction"]),
        entry_date=row["entry_date"],
        state=PositionState(
            symbol=row["symbol"],
            qty=row["qty"],
            entry_cost_per_unit=row["entry_cost_per_unit"],
            scaled_out=row["scaled_out"],
            peak_gain_pct=row["peak_gain_pct"],
        ),
    )


class StockPositionRepository:
    """One tracked stock position per symbol — matches how stock_entry_cycle
    checks for an existing position (either stock or options) before opening
    a new one."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(self, record: OpenStockPositionRecord, updated_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                record.symbol,
                record.direction.value,
                record.entry_date,
                record.state.qty,
                record.state.entry_cost_per_unit,
                record.state.scaled_out,
                record.state.peak_gain_pct,
                updated_at,
            )

    async def get(self, symbol: str) -> OpenStockPositionRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_GET_SQL, symbol)
        return _row_to_record(row) if row else None

    async def get_all(self) -> list[OpenStockPositionRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_GET_ALL_SQL)
        return [_row_to_record(r) for r in rows]

    async def delete(self, symbol: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SQL, symbol)
