import pytest

from config.settings import Settings
from data.database import Database


@pytest.mark.asyncio
async def test_connect_and_disconnect(dsn):
    db = Database(dsn=dsn)
    await db.connect()
    assert (await db.pool.fetchval("SELECT 1")) == 1
    await db.disconnect()


@pytest.mark.asyncio
async def test_pool_property_raises_before_connect(dsn):
    db = Database(dsn=dsn)
    with pytest.raises(RuntimeError, match="not connected"):
        _ = db.pool


@pytest.mark.asyncio
async def test_async_context_manager_connects_and_disconnects(dsn):
    async with Database(dsn=dsn) as db:
        assert (await db.pool.fetchval("SELECT 1")) == 1


@pytest.mark.asyncio
async def test_from_settings_uses_configured_dsn(dsn):
    settings = Settings(_env_file=None, database_url=dsn)
    db = Database.from_settings(settings)
    await db.connect()
    assert (await db.pool.fetchval("SELECT 1")) == 1
    await db.disconnect()
