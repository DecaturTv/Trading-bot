import asyncpg

from broker.models import OrderSide

from .models import OpenForexPosition

_COLUMNS = "pair, side, units, entry_price, stop_loss_price, take_profit_price, oanda_trade_id, opened_at"

_UPSERT_SQL = f"""
INSERT INTO forex_positions ({_COLUMNS})
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (pair) DO UPDATE SET
    side = EXCLUDED.side,
    units = EXCLUDED.units,
    entry_price = EXCLUDED.entry_price,
    stop_loss_price = EXCLUDED.stop_loss_price,
    take_profit_price = EXCLUDED.take_profit_price,
    oanda_trade_id = EXCLUDED.oanda_trade_id,
    opened_at = EXCLUDED.opened_at
"""

_GET_SQL = f"SELECT {_COLUMNS} FROM forex_positions WHERE pair = $1"
_GET_ALL_SQL = f"SELECT {_COLUMNS} FROM forex_positions ORDER BY pair"
_DELETE_SQL = "DELETE FROM forex_positions WHERE pair = $1"


def _row_to_record(row) -> OpenForexPosition:
    return OpenForexPosition(
        pair=row["pair"],
        side=OrderSide(row["side"]),
        units=row["units"],
        entry_price=row["entry_price"],
        stop_loss_price=row["stop_loss_price"],
        take_profit_price=row["take_profit_price"],
        oanda_trade_id=row["oanda_trade_id"],
        opened_at=row["opened_at"],
    )


class ForexPositionRepository:
    """One tracked position per pair — matches how forex_entry_cycle checks
    for an existing position before opening a new one."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(self, position: OpenForexPosition) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                position.pair,
                position.side.value,
                position.units,
                position.entry_price,
                position.stop_loss_price,
                position.take_profit_price,
                position.oanda_trade_id,
                position.opened_at,
            )

    async def get(self, pair: str) -> OpenForexPosition | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_GET_SQL, pair)
        return _row_to_record(row) if row else None

    async def get_all(self) -> list[OpenForexPosition]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_GET_ALL_SQL)
        return [_row_to_record(r) for r in rows]

    async def delete(self, pair: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SQL, pair)
