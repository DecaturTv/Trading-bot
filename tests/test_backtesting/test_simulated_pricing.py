from datetime import date

import pytest

from broker.models import OptionRight, OrderSide
from options.greeks import black_scholes
from backtesting.simulated_pricing import (
    SimulatedLeg,
    build_synthetic_chain,
    select_synthetic_strike_by_delta,
    simulated_strategy_value,
)

EXPIRY = date(2026, 8, 21)
AS_OF = date(2026, 7, 21)


def test_single_leg_value_matches_black_scholes_directly():
    leg = SimulatedLeg(strike=100.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY)
    value = simulated_strategy_value([leg], underlying_price=105.0, as_of=AS_OF, volatility=0.3)

    t = (EXPIRY - AS_OF).days / 365
    expected = black_scholes(105.0, 100.0, t, 0.3, OptionRight.CALL).price * 100
    assert value == pytest.approx(expected)


def test_short_leg_value_is_negated():
    long_leg = SimulatedLeg(strike=100.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY)
    short_leg = SimulatedLeg(strike=100.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.SELL)

    long_value = simulated_strategy_value([long_leg], 105.0, AS_OF, 0.3)
    short_value = simulated_strategy_value([short_leg], 105.0, AS_OF, 0.3)

    assert short_value == pytest.approx(-long_value)


def test_spread_value_is_difference_of_legs():
    long_leg = SimulatedLeg(strike=100.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY)
    short_leg = SimulatedLeg(strike=110.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.SELL)

    spread_value = simulated_strategy_value([long_leg, short_leg], 105.0, AS_OF, 0.3)
    long_only = simulated_strategy_value([long_leg], 105.0, AS_OF, 0.3)
    short_only = simulated_strategy_value([short_leg], 105.0, AS_OF, 0.3)

    assert spread_value == pytest.approx(long_only + short_only)


def test_build_synthetic_chain_deltas_decrease_with_strike_for_calls():
    chain = build_synthetic_chain(100.0, EXPIRY, AS_OF, 0.3, OptionRight.CALL, strike_increment=5.0)
    deltas_by_strike = dict(chain)
    strikes_sorted = sorted(deltas_by_strike)
    deltas_sorted = [deltas_by_strike[s] for s in strikes_sorted]
    assert deltas_sorted == sorted(deltas_sorted, reverse=True)  # monotonically decreasing


def test_select_synthetic_strike_by_delta_picks_closest():
    chain = [(90.0, 0.80), (100.0, 0.50), (110.0, 0.20)]
    assert select_synthetic_strike_by_delta(chain, target_delta=0.30) == 110.0


def test_select_synthetic_strike_by_delta_handles_puts():
    chain = [(90.0, -0.20), (100.0, -0.50), (110.0, -0.80)]
    assert select_synthetic_strike_by_delta(chain, target_delta=-0.25) == 90.0


def test_build_synthetic_chain_rejects_non_positive_increment():
    with pytest.raises(ValueError):
        build_synthetic_chain(100.0, EXPIRY, AS_OF, 0.3, OptionRight.CALL, strike_increment=0.0)


def test_select_synthetic_strike_by_delta_rejects_empty_chain():
    with pytest.raises(ValueError):
        select_synthetic_strike_by_delta([], target_delta=0.3)
