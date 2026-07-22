import json
from datetime import datetime

import asyncpg

from .models import FeatureSnapshot

_INSERT_SQL = """
INSERT INTO ml_feature_snapshots (symbol, as_of, factors, confidence, direction)
VALUES ($1, $2, $3::jsonb, $4, $5)
RETURNING id
"""

_RECORD_OUTCOME_SQL = "UPDATE ml_feature_snapshots SET pnl = $2, win = $3 WHERE id = $1"

_LABELED_DATASET_SQL = """
SELECT id, symbol, as_of, factors, confidence, direction, pnl, win
FROM ml_feature_snapshots
WHERE pnl IS NOT NULL
ORDER BY as_of
"""


class FeatureStoreRepository:
    """Records the feature vector behind each trade signal at decision time
    (outcome unknown yet), then gets updated once the trade closes — the
    accumulated (features, outcome) rows are training/'s dataset.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record_snapshot(
        self, symbol: str, as_of: datetime, factors: dict[str, float], confidence: float, direction: str
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_INSERT_SQL, symbol, as_of, json.dumps(factors), confidence, direction)
        return row["id"]

    async def record_outcome(self, snapshot_id: int, pnl: float) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_RECORD_OUTCOME_SQL, snapshot_id, pnl, pnl > 0)

    async def get_labeled_dataset(self) -> list[FeatureSnapshot]:
        async with self._pool.acquire() as conn:
            records = await conn.fetch(_LABELED_DATASET_SQL)
        return [
            FeatureSnapshot(
                id=r["id"],
                symbol=r["symbol"],
                as_of=r["as_of"],
                factors=json.loads(r["factors"]),
                confidence=r["confidence"],
                direction=r["direction"],
                pnl=r["pnl"],
                win=r["win"],
            )
            for r in records
        ]
