import asyncpg
import pytest

from decision_engine.signal_confirmation_schema import apply_signal_confirmation_schema

TEST_DSN = "postgresql://trading_bot:trading_bot@127.0.0.1:5432/trading_bot_test"


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN)
    await apply_signal_confirmation_schema(p)
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE TABLE signal_confirmation_state")
    yield p
    await p.close()
