from datetime import datetime, timezone

import pytest

from ml.models import FeatureSnapshot
from ml.training import MIN_LABELED_EXAMPLES, train_win_probability_model


def make_snapshot(i, factors, win):
    return FeatureSnapshot(
        id=i,
        symbol="AAPL",
        as_of=datetime.now(timezone.utc),
        factors=factors,
        confidence=80.0,
        direction="bullish",
        pnl=100.0 if win else -50.0,
        win=win,
    )


def make_labeled_dataset(n=40):
    return [
        make_snapshot(i, {"momentum": 0.8 if i % 2 == 0 else -0.8, "trend": 0.4 if i % 2 == 0 else -0.4}, i % 2 == 0)
        for i in range(n)
    ]


def test_trains_and_evaluates_on_a_learnable_pattern():
    result = train_win_probability_model(make_labeled_dataset(40), test_size=0.25, random_state=1)

    assert result.feature_names == ["momentum", "trend"]
    assert result.train_size + result.test_size == 40
    assert 0.0 <= result.accuracy <= 1.0
    assert result.accuracy > 0.7  # win is a deterministic function of momentum's sign
    assert result.auc is not None


def test_excludes_unlabeled_snapshots_from_training():
    labeled = make_labeled_dataset(20)
    unlabeled = FeatureSnapshot(
        id=999, symbol="X", as_of=datetime.now(timezone.utc), factors={"momentum": 0.1}, confidence=50.0,
        direction="bullish",
    )

    result = train_win_probability_model(labeled + [unlabeled], test_size=0.25, random_state=1)

    assert result.train_size + result.test_size == 20


def test_rejects_insufficient_labeled_examples():
    with pytest.raises(ValueError, match="at least"):
        train_win_probability_model(make_labeled_dataset(MIN_LABELED_EXAMPLES - 1))


def test_handles_inconsistent_feature_keys_across_snapshots():
    snapshots = [
        make_snapshot(i, {"momentum": 0.5} if i % 2 == 0 else {"trend": 0.5}, i % 2 == 0) for i in range(20)
    ]

    result = train_win_probability_model(snapshots, test_size=0.25, random_state=1)

    assert set(result.feature_names) == {"momentum", "trend"}
