import json
from datetime import date, datetime

import asyncpg

from broker.models import OptionRight, OrderSide
from decision_engine.models import TradeDirection
from options.models import StrategyType

from .models import OpenPositionRecord, PersistedLeg, PositionState

_UPSERT_SQL = """
INSERT INTO {table}
    (symbol, strategy_type, direction, entry_date, legs, qty, entry_cost_per_unit, scaled_out, peak_gain_pct, stop_loss_streak, reversal_streak, trailing_stop_streak, updated_at)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12, $13)
ON CONFLICT (symbol) DO UPDATE SET
    strategy_type = EXCLUDED.strategy_type,
    direction = EXCLUDED.direction,
    entry_date = EXCLUDED.entry_date,
    legs = EXCLUDED.legs,
    qty = EXCLUDED.qty,
    entry_cost_per_unit = EXCLUDED.entry_cost_per_unit,
    scaled_out = EXCLUDED.scaled_out,
    peak_gain_pct = EXCLUDED.peak_gain_pct,
    stop_loss_streak = EXCLUDED.stop_loss_streak,
    reversal_streak = EXCLUDED.reversal_streak,
    trailing_stop_streak = EXCLUDED.trailing_stop_streak,
    updated_at = EXCLUDED.updated_at
"""

_COLUMNS = (
    "symbol, strategy_type, direction, entry_date, legs, qty, entry_cost_per_unit, scaled_out, peak_gain_pct, "
    "stop_loss_streak, reversal_streak, trailing_stop_streak"
)


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
            stop_loss_streak=row["stop_loss_streak"],
            reversal_streak=row["reversal_streak"],
            trailing_stop_streak=row["trailing_stop_streak"],
        ),
    )


class PositionStateRepository:
    """One tracked strategy per underlying symbol — matches risk/'s default
    max_positions_per_symbol=1. If that cap is ever raised, this repository
    needs a compound key (e.g. symbol + entry order id) to track multiple
    concurrent positions in the same underlying.

    `table` selects the backing table so a second options strategy can keep
    its open positions apart from the default one (see
    trade_management/breakout_position_schema.py). Both tables share the same
    DDL, so all the (de)serialization here is reused unchanged.
    """

    def __init__(self, pool: asyncpg.Pool, table: str = "trade_management_positions"):
        if not table.replace("_", "").isalnum():
            raise ValueError(f"unsafe table name: {table!r}")
        self._pool = pool
        self._table = table
        self._upsert_sql = _UPSERT_SQL.format(table=table)
        self._get_sql = f"SELECT {_COLUMNS} FROM {table} WHERE symbol = $1"
        self._get_all_sql = f"SELECT {_COLUMNS} FROM {table} ORDER BY symbol"
        self._delete_sql = f"DELETE FROM {table} WHERE symbol = $1"

    async def upsert(self, record: OpenPositionRecord, updated_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                self._upsert_sql,
                record.symbol,
                record.strategy_type.value,
                record.direction.value,
                record.entry_date,
                _serialize_legs(record.legs),
                record.state.qty,
                record.state.entry_cost_per_unit,
                record.state.scaled_out,
                record.state.peak_gain_pct,
                record.state.stop_loss_streak,
                record.state.reversal_streak,
                record.state.trailing_stop_streak,
                updated_at,
            )

    async def get(self, symbol: str) -> OpenPositionRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(self._get_sql, symbol)
        return _row_to_record(row) if row else None

    async def get_all(self) -> list[OpenPositionRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(self._get_all_sql)
        return [_row_to_record(r) for r in rows]

    async def delete(self, symbol: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(self._delete_sql, symbol)
