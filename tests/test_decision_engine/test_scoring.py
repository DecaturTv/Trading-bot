import pytest

from decision_engine.models import TradeDirection
from decision_engine.scoring import WeightedFactorModel


def patch_factors(monkeypatch, **values):
    """Patches the internal factor dispatch table so scoring/aggregation logic
    can be tested independently of the factor math (covered in test_factors.py)."""
    import decision_engine.scoring as scoring_module

    for name in scoring_module.DEFAULT_WEIGHTS:
        value = values.get(name)
        monkeypatch.setitem(
            scoring_module._FACTOR_FUNCTIONS, name, lambda bars, scan_hits, congress_trades, tracked_members, v=value: v
        )


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
    # Only "gap" (weight 0.10 of 1.0 total configured) is available — below
    # the default 0.5 coverage requirement, so the score isn't trustworthy
    # enough to act on. Uses an explicit multi-factor weights dict rather
    # than the module default (DEFAULT_WEIGHTS is momentum-only as of
    # 2026-08-21, which can't exercise partial coverage at all — coverage
    # would just be binary 0 or 1 with a single configured factor).
    patch_factors(monkeypatch, gap=1.0)
    model = WeightedFactorModel(
        weights={"momentum": 0.30, "trend": 0.30, "macd": 0.20, "unusual_volume": 0.10, "gap": 0.10}
    )

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


def test_forex_weights_zero_out_unusual_volume(monkeypatch):
    import decision_engine.scoring as scoring_module

    # FOREX_WEIGHTS is deliberately decoupled from DEFAULT_WEIGHTS (see
    # scoring.py comment) so equities-side reweighting can't silently change
    # forex too -- assert its own fixed values directly rather than
    # comparing against whatever DEFAULT_WEIGHTS currently is.
    assert scoring_module.FOREX_WEIGHTS == {
        "momentum": 0.16,
        "trend": 0.20,
        "macd": 0.12,
        "unusual_volume": 0.0,
        "gap": 0.08,
        "candlestick": 0.12,
        "congress": 0.20,
    }


def test_forex_coverage_floor_rejects_the_collinear_momentum_trio_alone(monkeypatch):
    import decision_engine.scoring as scoring_module

    # Only momentum/trend/macd fire (gap/candlestick/congress unavailable, as
    # is typical on an FX candle). Their combined weight is 0.48 of the 0.88
    # configured -> 0.545 coverage, below the 0.6 forex floor, so the score
    # falls back to NEUTRAL rather than entering on one bet counted thrice.
    patch_factors(monkeypatch, momentum=1.0, trend=1.0, macd=1.0)
    model = WeightedFactorModel(weights=scoring_module.FOREX_WEIGHTS, min_available_weight_fraction=0.6)

    signal = model.score("EUR_USD", bars=[], scan_hits=[], confidence_threshold=10.0)

    assert signal.direction is TradeDirection.NEUTRAL
    assert signal.meets_threshold is False


def test_forex_coverage_floor_passes_once_an_independent_factor_agrees(monkeypatch):
    import decision_engine.scoring as scoring_module

    # momentum trio + a candlestick pattern -> 0.60 / 0.88 = 0.68 coverage,
    # clears the 0.6 floor, so this is a tradeable signal.
    patch_factors(monkeypatch, momentum=1.0, trend=1.0, macd=1.0, candlestick=1.0)
    model = WeightedFactorModel(weights=scoring_module.FOREX_WEIGHTS, min_available_weight_fraction=0.6)

    signal = model.score("EUR_USD", bars=[], scan_hits=[], confidence_threshold=10.0)

    assert signal.direction is TradeDirection.BULLISH
    assert signal.meets_threshold is True


def test_unusual_volume_excluded_from_forex_scoring(monkeypatch):
    import decision_engine.scoring as scoring_module

    # unusual_volume strongly bearish, everything else strongly bullish --
    # with FOREX_WEIGHTS it should be excluded entirely (weight 0), so the
    # score is a pure blend of the other factors with no bearish drag.
    patch_factors(monkeypatch, momentum=1.0, trend=1.0, macd=1.0, unusual_volume=-1.0, gap=1.0, candlestick=1.0, congress=1.0)
    model = WeightedFactorModel(weights=scoring_module.FOREX_WEIGHTS)

    signal = model.score("EUR_USD", bars=[], scan_hits=[], confidence_threshold=10.0)

    assert signal.confidence == pytest.approx(100.0)
    assert signal.direction is TradeDirection.BULLISH
    assert all(f.name != "unusual_volume" for f in signal.factors)


def test_congress_factor_can_be_blended_without_dominating(monkeypatch):
    # Every other factor bearish, congress alone bullish -- with a modest
    # congress share (0.20, matching forex's fixed weighting -- DEFAULT_WEIGHTS
    # itself is momentum-only as of 2026-08-21, so this uses an explicit
    # weights dict rather than relying on the module default) it shouldn't be
    # enough to flip the overall direction, proving the blending logic wires
    # congress in (not ignored) without letting it override everything else.
    patch_factors(monkeypatch, momentum=-1.0, trend=-1.0, macd=-1.0, unusual_volume=-1.0, gap=-1.0, candlestick=-1.0, congress=1.0)
    weights = {"momentum": 0.16, "trend": 0.20, "macd": 0.12, "unusual_volume": 0.12, "gap": 0.08, "candlestick": 0.12, "congress": 0.20}
    model = WeightedFactorModel(weights=weights)

    signal = model.score("AAPL", bars=[], scan_hits=[], confidence_threshold=10.0)

    assert signal.direction is TradeDirection.BEARISH
    expected = abs(sum(w if name == "congress" else -w for name, w in model._weights.items())) * 100
    assert signal.confidence == pytest.approx(expected)


def test_congress_factor_receives_trades_and_tracked_members(monkeypatch):
    import decision_engine.scoring as scoring_module

    seen = {}

    def fake_congress(bars, scan_hits, congress_trades, tracked_members):
        seen["congress_trades"] = congress_trades
        seen["tracked_members"] = tracked_members
        return 0.5

    monkeypatch.setitem(scoring_module._FACTOR_FUNCTIONS, "congress", fake_congress)
    model = WeightedFactorModel(weights={"congress": 1.0})

    model.score(
        "AAPL", bars=[], scan_hits=[], confidence_threshold=10.0,
        congress_trades=["dummy-trade"], tracked_members=["Nancy Pelosi"],
    )

    assert seen["congress_trades"] == ["dummy-trade"]
    assert seen["tracked_members"] == ["Nancy Pelosi"]
