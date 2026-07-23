import asyncpg
import pytest

from scanner.optionable_schema import apply_optionable_schema
from scanner.universe_schema import apply_universe_schema

TEST_DSN = "postgresql://trading_bot:trading_bot@127.0.0.1:5432/trading_bot_test"


@pytest.fixture
def dsn() -> str:
    return TEST_DSN


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN)
    await apply_universe_schema(p)
    await apply_optionable_schema(p)
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE TABLE universe_snapshots")
        await conn.execute("TRUNCATE TABLE optionable_symbols_snapshot")
    yield p
    await p.close()
