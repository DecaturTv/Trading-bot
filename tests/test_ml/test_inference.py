from datetime import datetime, timezone

import pytest

from decision_engine.models import FactorScore
from ml.inference import factors_to_dict, predict_win_probability
from ml.models import FeatureSnapshot
from ml.training import train_win_probability_model


def test_factors_to_dict_converts_factor_scores():
    factors = [FactorScore(name="momentum", value=0.8, weight=0.25), FactorScore(name="trend", value=0.6, weight=0.30)]
    assert factors_to_dict(factors) == {"momentum": 0.8, "trend": 0.6}


def test_predict_win_probability_returns_value_in_unit_interval():
    snapshots = [
        FeatureSnapshot(
            id=i, symbol="AAPL", as_of=datetime.now(timezone.utc), factors={"momentum": 0.8 if i % 2 == 0 else -0.8},
            confidence=80.0, direction="bullish", pnl=100.0 if i % 2 == 0 else -50.0, win=i % 2 == 0,
        )
        for i in range(30)
    ]
    result = train_win_probability_model(snapshots, random_state=1)

    prob = predict_win_probability(result.model, result.feature_names, {"momentum": 0.8})

    assert 0.0 <= prob <= 1.0
    # the pattern is win <=> momentum > 0, so a strongly positive momentum
    # should predict a high win probability
    assert prob > 0.5


def test_predict_win_probability_fills_missing_features_with_zero():
    snapshots = [
        FeatureSnapshot(
            id=i, symbol="AAPL", as_of=datetime.now(timezone.utc), factors={"momentum": 0.8 if i % 2 == 0 else -0.8},
            confidence=80.0, direction="bullish", pnl=100.0 if i % 2 == 0 else -50.0, win=i % 2 == 0,
        )
        for i in range(30)
    ]
    result = train_win_probability_model(snapshots, random_state=1)

    prob = predict_win_probability(result.model, result.feature_names, {})  # no factors supplied
    assert 0.0 <= prob <= 1.0


def test_predict_win_probability_rejects_empty_feature_names():
    with pytest.raises(ValueError):
        predict_win_probability(model=None, feature_names=[], factors={"momentum": 0.5})
