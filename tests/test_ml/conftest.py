import os

import asyncpg
import pytest

from ml.feature_store_schema import apply_feature_store_schema
from ml.trade_outcome_schema import apply_trade_outcome_schema

TEST_DSN = "postgresql://trading_bot:trading_bot@127.0.0.1:5432/trading_bot_test"


@pytest.fixture(autouse=True)
def _restore_mlflow_tracking_uri_env():
    # mlflow.set_tracking_uri() sets MLFLOW_TRACKING_URI as a process env var
    # side effect (not just in-memory state), which would otherwise leak into
    # any Settings() constructed later in the same pytest session.
    original = os.environ.get("MLFLOW_TRACKING_URI")
    yield
    if original is None:
        os.environ.pop("MLFLOW_TRACKING_URI", None)
    else:
        os.environ["MLFLOW_TRACKING_URI"] = original


@pytest.fixture
def dsn() -> str:
    return TEST_DSN


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN)
    await apply_feature_store_schema(p)
    await apply_trade_outcome_schema(p)
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE TABLE ml_feature_snapshots")
        await conn.execute("TRUNCATE TABLE ml_trade_outcomes")
    yield p
    await p.close()
