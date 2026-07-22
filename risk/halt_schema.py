import asyncpg

_HALT_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS risk_halt_events (
    id SERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('halt', 'resume')),
    reason TEXT NOT NULL
)
"""


async def apply_halt_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_HALT_EVENTS_TABLE_SQL)
