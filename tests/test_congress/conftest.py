import asyncpg
import pytest

from congress.schema import apply_congress_schema

TEST_DSN = "postgresql://trading_bot:trading_bot@127.0.0.1:5432/trading_bot_test"


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN)
    await apply_congress_schema(p)
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE TABLE congress_trades")
        await conn.execute("TRUNCATE TABLE congress_sync_state")
    yield p
    await p.close()
