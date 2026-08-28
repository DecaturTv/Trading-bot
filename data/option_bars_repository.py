from datetime import date, datetime

import asyncpg

from broker.models import Bar, OptionContract, OptionRight

_UPSERT_BARS_SQL = """
INSERT INTO option_bars (occ_symbol, timeframe, ts, open, high, low, close, volume)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (occ_symbol, timeframe, ts) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume
"""

_SELECT_BARS_SQL = """
SELECT ts, open, high, low, close, volume
FROM option_bars
WHERE occ_symbol = $1 AND timeframe = $2 AND ts BETWEEN $3 AND $4
ORDER BY ts
"""

_LATEST_BAR_SQL = "SELECT MAX(ts) FROM option_bars WHERE occ_symbol = $1 AND timeframe = $2"

_UPSERT_CONTRACT_SQL = """
INSERT INTO option_contracts (occ_symbol, underlying, expiration, strike, contract_right)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (occ_symbol) DO NOTHING
"""

_SELECT_CONTRACTS_SQL = """
SELECT occ_symbol, underlying, expiration, strike, contract_right
FROM option_contracts
WHERE underlying = $1 AND expiration BETWEEN $2 AND $3
ORDER BY expiration, strike
"""

# Contracts for an underlying that actually have at least one bar in the
# window — the set the backtest can realistically price against.
_SELECT_CONTRACTS_WITH_BARS_SQL = """
SELECT c.occ_symbol, c.underlying, c.expiration, c.strike, c.contract_right
FROM option_contracts c
WHERE c.underlying = $1
  AND c.expiration BETWEEN $2 AND $3
  AND EXISTS (
      SELECT 1 FROM option_bars b
      WHERE b.occ_symbol = c.occ_symbol AND b.timeframe = $4 AND b.ts BETWEEN $5 AND $6
  )
ORDER BY c.expiration, c.strike
"""

_COUNT_BARS_FOR_UNDERLYING_SQL = """
SELECT count(*)
FROM option_bars b
JOIN option_contracts c ON c.occ_symbol = b.occ_symbol
WHERE c.underlying = $1 AND b.timeframe = $2
"""


def _row_to_bar(occ_symbol: str, r) -> Bar:
    return Bar(
        symbol=occ_symbol,
        timestamp=r["ts"],
        open=r["open"],
        high=r["high"],
        low=r["low"],
        close=r["close"],
        volume=r["volume"],
    )


def _row_to_contract(r) -> OptionContract:
    return OptionContract(
        symbol=r["occ_symbol"],
        underlying_symbol=r["underlying"],
        strike=r["strike"],
        expiration=r["expiration"],
        right=OptionRight(r["contract_right"]),
        bid=None,
        ask=None,
        last_price=None,
        implied_volatility=None,
        greeks=None,
    )


class OptionBarsRepository:
    """Historical option OHLCV + contract reference data. Mirrors
    data.bars_repository.BarsRepository; kept separate so option (OCC) symbols
    don't mix into the equity/forex `bars` table."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert_option_bars(self, timeframe: str, bars: list[Bar]) -> int:
        if not bars:
            return 0
        rows = [(b.symbol, timeframe, b.timestamp, b.open, b.high, b.low, b.close, b.volume) for b in bars]
        async with self._pool.acquire() as conn:
            await conn.executemany(_UPSERT_BARS_SQL, rows)
        return len(rows)

    async def upsert_contracts(self, contracts: list[OptionContract]) -> int:
        if not contracts:
            return 0
        rows = [
            (c.symbol, c.underlying_symbol, c.expiration, c.strike, c.right.value)
            for c in contracts
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(_UPSERT_CONTRACT_SQL, rows)
        return len(rows)

    async def get_option_bars(self, occ_symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(_SELECT_BARS_SQL, occ_symbol, timeframe, start, end)
        return [_row_to_bar(occ_symbol, r) for r in records]

    async def latest_timestamp(self, occ_symbol: str, timeframe: str) -> datetime | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(_LATEST_BAR_SQL, occ_symbol, timeframe)

    async def get_contracts(self, underlying: str, expiration_gte: date, expiration_lte: date) -> list[OptionContract]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(_SELECT_CONTRACTS_SQL, underlying, expiration_gte, expiration_lte)
        return [_row_to_contract(r) for r in records]

    async def get_contracts_with_bars(
        self, underlying: str, timeframe: str, expiration_gte: date, expiration_lte: date, start: datetime, end: datetime
    ) -> list[OptionContract]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                _SELECT_CONTRACTS_WITH_BARS_SQL, underlying, expiration_gte, expiration_lte, timeframe, start, end
            )
        return [_row_to_contract(r) for r in records]

    async def bar_count_for_underlying(self, underlying: str, timeframe: str) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(_COUNT_BARS_FOR_UNDERLYING_SQL, underlying, timeframe) or 0
