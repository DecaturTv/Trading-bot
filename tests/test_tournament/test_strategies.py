import pytest

from decision_engine.scoring import WeightedFactorModel
from tournament.strategies import STRATEGIES, by_name


def test_every_strategy_builds_a_valid_factor_model():
    for strategy in STRATEGIES:
        # Raises on unknown factor names / bad coverage -> asserts the presets
        # only reference real decision_engine factors.
        WeightedFactorModel(weights=strategy.weights, min_available_weight_fraction=strategy.min_coverage)


def test_no_strategy_weights_the_unscoreable_congress_factor():
    # congress can't be scored in either backtest engine (no disclosure data
    # is passed to score()), so competing on it would be dead weight.
    for strategy in STRATEGIES:
        assert "congress" not in strategy.weights


def test_strategy_names_are_unique():
    names = [s.name for s in STRATEGIES]
    assert len(names) == len(set(names))


def test_by_name_is_case_and_whitespace_insensitive():
    assert by_name("  momentum rider ") is by_name("MOMENTUM RIDER")


def test_by_name_rejects_unknown():
    with pytest.raises(KeyError):
        by_name("Nonexistent Strategy")


def test_knob_ranges_are_sane():
    for s in STRATEGIES:
        assert 0 <= s.equities.confidence_threshold <= 100
        assert 0 < s.equities.target_delta <= 1
        assert s.equities.target_dte > 0
        assert s.equities.stop_loss_pct > 0
        assert s.equities.profit_target_dollars > 0
        assert 0 < s.equities.kelly_fraction <= 1
        assert 0 <= s.forex.confidence_threshold <= 100
        assert 0 < s.forex.risk_pct_per_trade <= 1
        assert s.forex.stop_atr_multiplier > 0
        assert s.forex.take_profit_r_multiple > 0
