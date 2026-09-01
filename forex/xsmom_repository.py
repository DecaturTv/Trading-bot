from dataclasses import dataclass
from datetime import date, datetime

import asyncpg

from decision_engine.models import TradeDirection

_COLS = "pair, direction, units, entry_price, score, oanda_trade_id, opened_at"


@dataclass(frozen=True)
class XsmomPosition:
    pair: str
    direction: TradeDirection
    units: int
    entry_price: float
    score: float
    oanda_trade_id: str
    opened_at: datetime


def _row(r) -> XsmomPosition:
    return XsmomPosition(
        pair=r["pair"],
        direction=TradeDirection(r["direction"]),
        units=r["units"],
        entry_price=r["entry_price"],
        score=r["score"],
        oanda_trade_id=r["oanda_trade_id"],
        opened_at=r["opened_at"],
    )


class ForexXsmomRepository:
    """Positions + last-rebalance date for the cross-sectional momentum book."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_all(self) -> list[XsmomPosition]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT {_COLS} FROM forex_xsmom_positions ORDER BY pair")
        return [_row(r) for r in rows]

    async def upsert(self, pos: XsmomPosition) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO forex_xsmom_positions ({_COLS})
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (pair) DO UPDATE SET
                      direction=EXCLUDED.direction, units=EXCLUDED.units,
                      entry_price=EXCLUDED.entry_price, score=EXCLUDED.score,
                      oanda_trade_id=EXCLUDED.oanda_trade_id, opened_at=EXCLUDED.opened_at""",
                pos.pair, pos.direction.value, pos.units, pos.entry_price,
                pos.score, pos.oanda_trade_id, pos.opened_at,
            )

    async def delete(self, pair: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM forex_xsmom_positions WHERE pair = $1", pair)

    async def last_rebalance_date(self) -> date | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT last_rebalance_date FROM forex_xsmom_state WHERE id = 1")
        return row["last_rebalance_date"] if row else None

    async def set_last_rebalance_date(self, d: date) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO forex_xsmom_state (id, last_rebalance_date) VALUES (1, $1) "
                "ON CONFLICT (id) DO UPDATE SET last_rebalance_date = EXCLUDED.last_rebalance_date",
                d,
            )
