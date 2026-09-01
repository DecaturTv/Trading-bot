import pytest

from decision_engine.models import TradeDirection
from forex.cross_sectional import (
    TargetLeg,
    leg_units,
    momentum_scores,
    rebalance_plan,
    target_book,
)


def test_momentum_scores_trailing_return():
    closes = {
        "EUR_USD": [1.0, 1.1, 1.2, 1.32],   # +32% over 3 bars from the -4th
        "USD_JPY": [150.0, 148.0, 146.0, 144.0],
    }
    scores = momentum_scores(closes, lookback_bars=3)
    assert scores["EUR_USD"] == pytest.approx(0.32)
    assert scores["USD_JPY"] == pytest.approx(144.0 / 150.0 - 1)


def test_momentum_scores_drops_pairs_with_thin_history():
    closes = {"A_B": [1.0, 1.1], "C_D": [1.0, 1.0, 1.0, 1.05]}
    scores = momentum_scores(closes, lookback_bars=3)
    assert "A_B" not in scores  # only 2 closes, need 4
    assert "C_D" in scores


def test_momentum_scores_drops_non_positive_prices():
    closes = {"A_B": [0.0, 1.0, 1.0, 1.0], "C_D": [1.0, 1.0, 1.0, -0.5]}
    assert momentum_scores(closes, lookback_bars=3) == {}


def test_target_book_longs_top_shorts_bottom():
    scores = {"P1": 0.5, "P2": 0.3, "P3": 0.0, "P4": -0.2, "P5": -0.4, "P6": -0.6}
    legs = target_book(scores, top_k=2)
    longs = {leg.pair for leg in legs if leg.direction is TradeDirection.BULLISH}
    shorts = {leg.pair for leg in legs if leg.direction is TradeDirection.BEARISH}
    assert longs == {"P1", "P2"}
    assert shorts == {"P5", "P6"}
    assert all(leg.weight == pytest.approx(0.25) for leg in legs)  # 1/(2*2)
    assert abs(sum(leg.weight for leg in legs) - 1.0) < 1e-9


def test_target_book_empty_when_too_few_pairs():
    assert target_book({"P1": 0.1, "P2": -0.1, "P3": 0.0}, top_k=2) == []


def test_leg_units_scales_notional_by_weight_and_price():
    leg = TargetLeg("EUR_USD", TradeDirection.BULLISH, weight=0.1, score=0.2)
    # equity 3000 * gross 1.0 * weight 0.1 = $300 notional; EUR_USD mid 1.10,
    # USD account so rate 1.0 -> 300 / 1.10 = 272 units
    assert leg_units(3000.0, 1.0, leg, mid_price=1.10, quote_to_account_rate=1.0) == 272


def test_leg_units_applies_quote_conversion():
    leg = TargetLeg("EUR_GBP", TradeDirection.BEARISH, weight=0.1, score=-0.2)
    # $300 notional; EUR_GBP mid 0.85 (GBP per EUR), GBP->USD rate 1.25
    # unit value = 0.85 * 1.25 = 1.0625 USD -> 300 / 1.0625 = 282
    assert leg_units(3000.0, 1.0, leg, mid_price=0.85, quote_to_account_rate=1.25) == 282


def test_leg_units_can_be_zero_on_a_tiny_account():
    leg = TargetLeg("USD_JPY", TradeDirection.BULLISH, weight=0.1, score=0.1)
    assert leg_units(10.0, 1.0, leg, mid_price=150.0, quote_to_account_rate=1.0) == 0


def test_leg_units_rejects_bad_inputs():
    leg = TargetLeg("EUR_USD", TradeDirection.BULLISH, 0.1, 0.0)
    with pytest.raises(ValueError):
        leg_units(-1.0, 1.0, leg, 1.1, 1.0)
    with pytest.raises(ValueError):
        leg_units(3000.0, 1.0, leg, 0.0, 1.0)


def test_rebalance_plan_full_rebuild():
    legs = [
        TargetLeg("P1", TradeDirection.BULLISH, 0.5, 0.3),
        TargetLeg("P2", TradeDirection.BEARISH, 0.5, -0.3),
    ]
    to_close, to_open = rebalance_plan(legs, current_pairs={"P2", "P9"})
    assert to_close == ["P2", "P9"]           # everything currently held is closed
    assert to_open == {"P1", "P2"}            # whole new book opened fresh
