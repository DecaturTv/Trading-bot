import json
from datetime import date, datetime

import asyncpg

from broker.models import OptionRight, OrderSide
from decision_engine.models import TradeDirection
from options.models import StrategyType

from .models import OpenPositionRecord, PersistedLeg, PositionState

_UPSERT_SQL = """
INSERT INTO trade_management_positions
    (symbol, strategy_type, direction, entry_date, legs, qty, entry_cost_per_unit, scaled_out, peak_gain_pct, updated_at)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
ON CONFLICT (symbol) DO UPDATE SET
    strategy_type = EXCLUDED.strategy_type,
    direction = EXCLUDED.direction,
    entry_date = EXCLUDED.entry_date,
    legs = EXCLUDED.legs,
    qty = EXCLUDED.qty,
    entry_cost_per_unit = EXCLUDED.entry_cost_per_unit,
    scaled_out = EXCLUDED.scaled_out,
    peak_gain_pct = EXCLUDED.peak_gain_pct,
    updated_at = EXCLUDED.updated_at
"""

_COLUMNS = "symbol, strategy_type, direction, entry_date, legs, qty, entry_cost_per_unit, scaled_out, peak_gain_pct"
_GET_SQL = f"SELECT {_COLUMNS} FROM trade_management_positions WHERE symbol = $1"
_GET_ALL_SQL = f"SELECT {_COLUMNS} FROM trade_management_positions ORDER BY symbol"
_DELETE_SQL = "DELETE FROM trade_management_positions WHERE symbol = $1"


def _serialize_legs(legs: list[PersistedLeg]) -> str:
    return json.dumps(
        [
            {
                "symbol": leg.symbol,
                "strike": leg.strike,
                "expiration": leg.expiration.isoformat(),
                "right": leg.right.value,
                "side": leg.side.value,
            }
            for leg in legs
        ]
    )


def _deserialize_legs(raw: str) -> list[PersistedLeg]:
    return [
        PersistedLeg(
            symbol=d["symbol"],
            strike=d["strike"],
            expiration=date.fromisoformat(d["expiration"]),
            right=OptionRight(d["right"]),
            side=OrderSide(d["side"]),
        )
        for d in json.loads(raw)
    ]


def _row_to_record(row) -> OpenPositionRecord:
    return OpenPositionRecord(
        symbol=row["symbol"],
        strategy_type=StrategyType(row["strategy_type"]),
        direction=TradeDirection(row["direction"]),
        entry_date=row["entry_date"],
        legs=_deserialize_legs(row["legs"]),
        state=PositionState(
            symbol=row["symbol"],
            qty=row["qty"],
            entry_cost_per_unit=row["entry_cost_per_unit"],
            scaled_out=row["scaled_out"],
            peak_gain_pct=row["peak_gain_pct"],
        ),
    )


class PositionStateRepository:
    """One tracked strategy per underlying symbol — matches risk/'s default
    max_positions_per_symbol=1. If that cap is ever raised, this repository
    needs a compound key (e.g. symbol + entry order id) to track multiple
    concurrent positions in the same underlying.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert(self, record: OpenPositionRecord, updated_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                record.symbol,
                record.strategy_type.value,
                record.direction.value,
                record.entry_date,
                _serialize_legs(record.legs),
                record.state.qty,
                record.state.entry_cost_per_unit,
                record.state.scaled_out,
                record.state.peak_gain_pct,
                updated_at,
            )

    async def get(self, symbol: str) -> OpenPositionRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_GET_SQL, symbol)
        return _row_to_record(row) if row else None

    async def get_all(self) -> list[OpenPositionRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_GET_ALL_SQL)
        return [_row_to_record(r) for r in rows]

    async def delete(self, symbol: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_DELETE_SQL, symbol)
