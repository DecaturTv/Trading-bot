from datetime import datetime

import asyncpg

from .models import PositionState

_UPSERT_SQL = """
INSERT INTO trade_management_positions (symbol, qty, entry_cost_per_unit, scaled_out, peak_gain_pct, updated_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (symbol) DO UPDATE SET
    qty = EXCLUDED.qty,
    entry_cost_per_unit = EXCLUDED.entry_cost_per_unit,
    scaled_out = EXCLUDED.scaled_out,
    peak_gain_pct = EXCLUDED.peak_gain_pct,
    updated_at = EXCLUDED.updated_at
"""

_GET_SQL = "SELECT qty, entry_cost_per_unit, scaled_out, peak_gain_pct FROM trade_management_positions WHERE symbol = $1"

_DELETE_SQL = "DELETE FROM trade_management_positions WHERE symbol = $1"


class PositionStateRepository:
    """One tracked strategy per underlying symbol — matches risk/'s default
    max_positions_per_symbol=1. If that cap is ever raised, this repository
    needs a compound key (e.g. symbol + entry order id) to track multiple
    concurrent positions in the same underlying.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(self, state: PositionState, updated_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                state.symbol,
                state.qty,
                state.entry_cost_per_unit,
                state.scaled_out,
                state.peak_gain_pct,
                updated_at,
            )

    async def get(self, symbol: str) -> PositionState | None:
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(_GET_SQL, symbol)
        if record is None:
            return None
        return PositionState(
            symbol=symbol,
            qty=record["qty"],
            entry_cost_per_unit=record["entry_cost_per_unit"],
            scaled_out=record["scaled_out"],
            peak_gain_pct=record["peak_gain_pct"],
        )

    async def delete(self, symbol: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SQL, symbol)
