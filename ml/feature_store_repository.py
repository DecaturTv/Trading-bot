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

# symbol_pattern lets callers scope the labeled dataset to one asset class
# without a dedicated column on this table -- OANDA pairs are always
# THREE_THREE (e.g. EUR_USD) which no equities/options symbol matches, the
# same regex used to backfill asset_class on ml_trade_outcomes.
_LABELED_DATASET_BY_SYMBOL_PATTERN_SQL = """
SELECT id, symbol, as_of, factors, confidence, direction, pnl, win
FROM ml_feature_snapshots
WHERE pnl IS NOT NULL AND symbol ~ $1
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

    async def get_labeled_dataset(self, symbol_pattern: str | None = None) -> list[FeatureSnapshot]:
        async with self._pool.acquire() as conn:
            if symbol_pattern is None:
                records = await conn.fetch(_LABELED_DATASET_SQL)
            else:
                records = await conn.fetch(_LABELED_DATASET_BY_SYMBOL_PATTERN_SQL, symbol_pattern)
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
