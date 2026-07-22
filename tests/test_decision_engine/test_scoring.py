import pytest

from decision_engine.models import TradeDirection
from decision_engine.scoring import WeightedFactorModel


def patch_factors(monkeypatch, **values):
    """Patches the internal factor dispatch table so scoring/aggregation logic
    can be tested independently of the factor math (covered in test_factors.py)."""
    import decision_engine.scoring as scoring_module

    for name in scoring_module.DEFAULT_WEIGHTS:
        value = values.get(name)
        monkeypatch.setitem(scoring_module._FACTOR_FUNCTIONS, name, lambda bars, scan_hits, v=value: v)


def test_score_combines_factors_as_weighted_average(monkeypatch):
    patch_factors(monkeypatch, momentum=0.8, trend=0.6, macd=0.4)
    model = WeightedFactorModel(
        weights={"momentum": 0.5, "trend": 0.3, "macd": 0.2, "unusual_volume": 0.0, "gap": 0.0}
    )

    signal = model.score("AAPL", bars=[], scan_hits=[], confidence_threshold=50.0)

    expected = (0.8 * 0.5 + 0.6 * 0.3 + 0.4 * 0.2) / (0.5 + 0.3 + 0.2) * 100
    assert signal.confidence == pytest.approx(expected)
    assert signal.direction is TradeDirection.BULLISH
    assert signal.meets_threshold == (expected >= 50.0)


def test_score_bearish_when_weighted_value_negative(monkeypatch):
    patch_factors(monkeypatch, momentum=-0.9)
    model = WeightedFactorModel(
        weights={"momentum": 1.0, "trend": 0.0, "macd": 0.0, "unusual_volume": 0.0, "gap": 0.0}
    )

    signal = model.score("AAPL", [], [], confidence_threshold=50.0)

    assert signal.direction is TradeDirection.BEARISH
    assert signal.confidence == pytest.approx(90.0)


def test_score_neutral_when_no_factors_available(monkeypatch):
    patch_factors(monkeypatch)
    model = WeightedFactorModel()

    signal = model.score("AAPL", [], [], confidence_threshold=50.0)

    assert signal.direction is TradeDirection.NEUTRAL
    assert signal.confidence == 0.0
    assert signal.meets_threshold is False


def test_score_neutral_when_factor_coverage_too_low(monkeypatch):
    # Only "gap" (weight 0.10 of 1.0 total) is available — below the default
    # 0.5 coverage requirement, so the score isn't trustworthy enough to act on.
    patch_factors(monkeypatch, gap=1.0)
    model = WeightedFactorModel()

    signal = model.score("AAPL", [], [], confidence_threshold=50.0)

    assert signal.direction is TradeDirection.NEUTRAL
    assert signal.confidence == 0.0


def test_score_proceeds_when_coverage_meets_minimum(monkeypatch):
    patch_factors(monkeypatch, momentum=0.5, trend=0.5)
    model = WeightedFactorModel(
        weights={"momentum": 0.25, "trend": 0.30, "macd": 0.20, "unusual_volume": 0.15, "gap": 0.10},
        min_available_weight_fraction=0.5,
    )

    signal = model.score("AAPL", [], [], confidence_threshold=10.0)

    assert signal.direction is TradeDirection.BULLISH
    assert signal.confidence > 0.0


def test_meets_threshold_boundary(monkeypatch):
    patch_factors(monkeypatch, momentum=0.92)
    model = WeightedFactorModel(
        weights={"momentum": 1.0, "trend": 0.0, "macd": 0.0, "unusual_volume": 0.0, "gap": 0.0}
    )

    signal = model.score("AAPL", [], [], confidence_threshold=92.0)

    assert signal.confidence == pytest.approx(92.0)
    assert signal.meets_threshold is True


def test_rejects_unknown_factor_name():
    with pytest.raises(ValueError, match="unknown factor"):
        WeightedFactorModel(weights={"covered_call_iv": 0.5})
