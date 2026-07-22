from datetime import datetime, timezone

import pytest

from ml.feature_store_repository import FeatureStoreRepository


@pytest.mark.asyncio
async def test_get_labeled_dataset_excludes_snapshots_without_outcomes(pool):
    repo = FeatureStoreRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.record_snapshot("AAPL", now, {"momentum": 0.8, "trend": 0.6}, confidence=85.0, direction="bullish")

    assert await repo.get_labeled_dataset() == []


@pytest.mark.asyncio
async def test_record_outcome_makes_snapshot_appear_in_labeled_dataset(pool):
    repo = FeatureStoreRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    snapshot_id = await repo.record_snapshot(
        "AAPL", now, {"momentum": 0.8, "trend": 0.6}, confidence=85.0, direction="bullish"
    )
    await repo.record_outcome(snapshot_id, pnl=150.0)

    dataset = await repo.get_labeled_dataset()
    assert len(dataset) == 1
    snapshot = dataset[0]
    assert snapshot.id == snapshot_id
    assert snapshot.symbol == "AAPL"
    assert snapshot.factors == {"momentum": 0.8, "trend": 0.6}
    assert snapshot.confidence == pytest.approx(85.0)
    assert snapshot.direction == "bullish"
    assert snapshot.pnl == pytest.approx(150.0)
    assert snapshot.win is True


@pytest.mark.asyncio
async def test_record_outcome_marks_loss_correctly(pool):
    repo = FeatureStoreRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    snapshot_id = await repo.record_snapshot("AAPL", now, {"momentum": -0.5}, confidence=70.0, direction="bearish")
    await repo.record_outcome(snapshot_id, pnl=-80.0)

    dataset = await repo.get_labeled_dataset()
    assert dataset[0].win is False
    assert dataset[0].pnl == pytest.approx(-80.0)


@pytest.mark.asyncio
async def test_labeled_dataset_ordered_by_as_of(pool):
    repo = FeatureStoreRepository(pool)
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 1, 8, tzinfo=timezone.utc)

    id_new = await repo.record_snapshot("TSLA", newer, {"momentum": 0.1}, confidence=50.0, direction="bullish")
    id_old = await repo.record_snapshot("AAPL", older, {"momentum": 0.2}, confidence=60.0, direction="bullish")
    await repo.record_outcome(id_new, pnl=10.0)
    await repo.record_outcome(id_old, pnl=20.0)

    dataset = await repo.get_labeled_dataset()
    assert [s.symbol for s in dataset] == ["AAPL", "TSLA"]
