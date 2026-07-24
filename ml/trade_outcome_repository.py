import json
from datetime import datetime

import asyncpg

_INSERT_SQL = "INSERT INTO ml_trade_outcomes (symbol, closed_at, pnl, asset_class, details) VALUES ($1, $2, $3, $4, $5::jsonb)"

_RECENT_PNLS_SQL = "SELECT pnl FROM ml_trade_outcomes ORDER BY closed_at DESC, id DESC LIMIT $1"
_RECENT_PNLS_BY_CLASS_SQL = (
    "SELECT pnl FROM ml_trade_outcomes WHERE asset_class = $2 ORDER BY closed_at DESC, id DESC LIMIT $1"
)
_ALL_PNLS_SQL = "SELECT pnl FROM ml_trade_outcomes ORDER BY closed_at"
_ALL_PNLS_BY_CLASS_SQL = "SELECT pnl FROM ml_trade_outcomes WHERE asset_class = $1 ORDER BY closed_at"
_PNLS_SINCE_SQL = "SELECT pnl FROM ml_trade_outcomes WHERE closed_at >= $1 ORDER BY closed_at"
_PNLS_SINCE_BY_CLASS_SQL = "SELECT pnl FROM ml_trade_outcomes WHERE closed_at >= $1 AND asset_class = $2 ORDER BY closed_at"

_TRADE_COLUMNS = "symbol, closed_at, pnl, asset_class, details"
_RECENT_TRADES_SQL = f"SELECT {_TRADE_COLUMNS} FROM ml_trade_outcomes ORDER BY closed_at DESC, id DESC LIMIT $1"
_RECENT_TRADES_BY_CLASS_SQL = (
    f"SELECT {_TRADE_COLUMNS} FROM ml_trade_outcomes WHERE asset_class = $2 ORDER BY closed_at DESC, id DESC LIMIT $1"
)
_ALL_TRADES_SQL = f"SELECT {_TRADE_COLUMNS} FROM ml_trade_outcomes ORDER BY closed_at DESC"
_ALL_TRADES_BY_CLASS_SQL = f"SELECT {_TRADE_COLUMNS} FROM ml_trade_outcomes WHERE asset_class = $1 ORDER BY closed_at DESC"


def _row_to_trade(row) -> dict:
    return {
        "symbol": row["symbol"],
        "closed_at": row["closed_at"],
        "pnl": row["pnl"],
        "asset_class": row["asset_class"],
        "details": json.loads(row["details"]),
    }


class TradeOutcomeRepository:
    """asset_class ('equities' or 'forex') keeps Kelly sizing, loss-limit
    checks, and progress reporting from mixing pnl history across the two
    independent accounts (Alpaca vs OANDA) -- a losing streak on one side
    shouldn't skew sizing or halt decisions on the other. Filtering is
    opt-in (None = unfiltered) at this layer; callers are expected to always
    pass the right asset_class.

    details is freeform per-trade context (entry/exit price, stop/target,
    direction, confidence, exit reason) -- without it, a closed trade was
    just a number with no way to ever ask why it won or lost.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_outcome(
        self, symbol: str, closed_at: datetime, pnl: float, asset_class: str = "equities", details: dict | None = None
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_INSERT_SQL, symbol, closed_at, pnl, asset_class, json.dumps(details or {}))

    async def recent_pnls(self, limit: int | None = None, asset_class: str | None = None) -> list[float]:
        async with self._pool.acquire() as conn:
            if limit is None:
                sql, args = (_ALL_PNLS_BY_CLASS_SQL, (asset_class,)) if asset_class else (_ALL_PNLS_SQL, ())
                records = await conn.fetch(sql, *args)
            else:
                sql = _RECENT_PNLS_BY_CLASS_SQL if asset_class else _RECENT_PNLS_SQL
                args = (limit, asset_class) if asset_class else (limit,)
                records = await conn.fetch(sql, *args)
        return [r["pnl"] for r in records]

    async def pnls_since(self, cutoff: datetime, asset_class: str | None = None) -> list[float]:
        async with self._pool.acquire() as conn:
            sql = _PNLS_SINCE_BY_CLASS_SQL if asset_class else _PNLS_SINCE_SQL
            args = (cutoff, asset_class) if asset_class else (cutoff,)
            records = await conn.fetch(sql, *args)
        return [r["pnl"] for r in records]

    async def recent_trades(self, limit: int | None = None, asset_class: str | None = None) -> list[dict]:
        """Full closed-trade records (including details) for post-mortem
        analysis -- recent_pnls/pnls_since only return the bare numbers
        Kelly sizing and loss-limit checks need."""
        async with self._pool.acquire() as conn:
            if limit is None:
                sql, args = (_ALL_TRADES_BY_CLASS_SQL, (asset_class,)) if asset_class else (_ALL_TRADES_SQL, ())
                records = await conn.fetch(sql, *args)
            else:
                sql = _RECENT_TRADES_BY_CLASS_SQL if asset_class else _RECENT_TRADES_SQL
                args = (limit, asset_class) if asset_class else (limit,)
                records = await conn.fetch(sql, *args)
        return [_row_to_trade(r) for r in records]
