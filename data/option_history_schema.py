import asyncpg

# Historical option OHLCV, keyed by OCC symbol (e.g. AAPL260701C00225000).
# Kept in its own table rather than reusing `bars` so option contract symbols
# never leak into the equity/forex universe queries (see tournament.runner
# ._symbols_for, which partitions `bars` by symbol shape).
_OPTION_BARS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS option_bars (
    occ_symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (occ_symbol, timeframe, ts)
)
"""

# Contract reference data, so the backtest can pick a strike for an underlying
# on a past date without a live chain call per bar. underlying is indexed for
# the per-underlying chain lookup.
_OPTION_CONTRACTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS option_contracts (
    occ_symbol TEXT PRIMARY KEY,
    underlying TEXT NOT NULL,
    expiration DATE NOT NULL,
    strike DOUBLE PRECISION NOT NULL,
    contract_right TEXT NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_OPTION_CONTRACTS_UNDERLYING_IDX_SQL = (
    "CREATE INDEX IF NOT EXISTS option_contracts_underlying_idx ON option_contracts (underlying, expiration)"
)

_OPTION_BARS_HYPERTABLE_SQL = (
    "SELECT create_hypertable('option_bars', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"
)
_HAS_TIMESCALEDB_SQL = "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')"


async def apply_option_history_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_OPTION_BARS_TABLE_SQL)
        await conn.execute(_OPTION_CONTRACTS_TABLE_SQL)
        await conn.execute(_OPTION_CONTRACTS_UNDERLYING_IDX_SQL)
        # Same as data.schema: production runs timescale/timescaledb; local dev
        # without the extension just keeps a plain table.
        if await conn.fetchval(_HAS_TIMESCALEDB_SQL):
            await conn.execute(_OPTION_BARS_HYPERTABLE_SQL)
