import pytest

from data.schema import apply_schema


@pytest.mark.asyncio
async def test_apply_schema_creates_bars_table(pool):
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'bars')"
        )
    assert exists is True


@pytest.mark.asyncio
async def test_apply_schema_is_idempotent(pool):
    await apply_schema(pool)
    await apply_schema(pool)
