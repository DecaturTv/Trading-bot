from datetime import datetime, timezone

import numpy as np
import pytest

from ml.models import FeatureSnapshot
from ml.registry import ModelRegistry
from ml.training import train_win_probability_model


def make_training_result():
    snapshots = [
        FeatureSnapshot(
            id=i, symbol="AAPL", as_of=datetime.now(timezone.utc), factors={"momentum": 0.8 if i % 2 == 0 else -0.8},
            confidence=80.0, direction="bullish", pnl=100.0 if i % 2 == 0 else -50.0, win=i % 2 == 0,
        )
        for i in range(30)
    ]
    return train_win_probability_model(snapshots, random_state=1)


@pytest.fixture
def tracking_uri(tmp_path):
    return f"sqlite:///{tmp_path / 'mlflow.db'}"


def test_log_and_load_model_roundtrip(tracking_uri):
    registry = ModelRegistry(tracking_uri, experiment_name="test-exp")
    result = make_training_result()

    run_id = registry.log_training_run(result, params={"kelly_fraction": 0.25})
    loaded_model = registry.load_model(run_id)
    feature_names = registry.get_feature_names(run_id)

    assert feature_names == result.feature_names
    assert np.allclose(result.model.predict_proba([[0.8]]), loaded_model.predict_proba([[0.8]]))


def test_get_latest_run_id_returns_most_recent(tracking_uri):
    registry = ModelRegistry(tracking_uri, experiment_name="test-exp")
    result = make_training_result()

    run_id_1 = registry.log_training_run(result)
    run_id_2 = registry.log_training_run(result)

    assert run_id_1 != run_id_2
    assert registry.get_latest_run_id() == run_id_2


def test_get_latest_run_id_none_when_no_runs_logged(tracking_uri):
    registry = ModelRegistry(tracking_uri, experiment_name="empty-exp")
    assert registry.get_latest_run_id() is None
