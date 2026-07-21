import asyncpg

_BARS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts)
)
"""

_HYPERTABLE_SQL = "SELECT create_hypertable('bars', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"

_HAS_TIMESCALEDB_SQL = "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')"


async def apply_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_BARS_TABLE_SQL)
        # Production runs the official timescale/timescaledb image, where this
        # extension is always present; local dev without it just keeps a plain table.
        has_timescaledb = await conn.fetchval(_HAS_TIMESCALEDB_SQL)
        if has_timescaledb:
            await conn.execute(_HYPERTABLE_SQL)
