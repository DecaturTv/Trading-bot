import asyncpg
import pytest

from risk.halt_schema import apply_halt_schema

TEST_DSN = "postgresql://trading_bot:trading_bot@127.0.0.1:5432/trading_bot_test"


@pytest.fixture
def dsn() -> str:
    return TEST_DSN


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN)
    await apply_halt_schema(p)
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE TABLE risk_halt_events")
    yield p
    await p.close()
