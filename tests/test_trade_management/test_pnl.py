from datetime import date

import pytest
from tm_factories import make_contract

from options.strategy_builders import build_debit_vertical_spread, build_long_call
from trade_management.pnl import current_value_per_unit, unrealized_gain_pct

NEAR = date(2026, 8, 21)


def test_unrealized_gain_pct_basic():
    assert unrealized_gain_pct(entry_cost_per_unit=500.0, current_value_per_unit=1000.0) == pytest.approx(1.0)
    assert unrealized_gain_pct(entry_cost_per_unit=500.0, current_value_per_unit=250.0) == pytest.approx(-0.5)


def test_unrealized_gain_pct_rejects_non_positive_entry_cost():
    with pytest.raises(ValueError):
        unrealized_gain_pct(entry_cost_per_unit=0.0, current_value_per_unit=100.0)


def test_current_value_per_unit_single_long_leg():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)

    current = make_contract("C1", 100, NEAR, bid=9.0, ask=11.0)
    value = current_value_per_unit(strategy, {"C1": current})

    assert value == pytest.approx(1000.0)  # mid=10.0 * 100


def test_current_value_per_unit_debit_spread():
    long_contract = make_contract("LONG", 100, NEAR, ask=5.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, NEAR, bid=2.0, delta=0.3)
    strategy = build_debit_vertical_spread(long_contract, short_contract)

    current_long = make_contract("LONG", 100, NEAR, bid=7.0, ask=8.0)  # mid 7.5
    current_short = make_contract("SHORT", 105, NEAR, bid=1.0, ask=1.5)  # mid 1.25

    value = current_value_per_unit(strategy, {"LONG": current_long, "SHORT": current_short})

    assert value == pytest.approx((7.5 - 1.25) * 100)


def test_current_value_per_unit_raises_when_quote_missing():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)

    with pytest.raises(ValueError):
        current_value_per_unit(strategy, {})


def test_current_value_per_unit_raises_when_bid_or_ask_missing():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)

    current = make_contract("C1", 100, NEAR, bid=None, ask=11.0)
    with pytest.raises(ValueError):
        current_value_per_unit(strategy, {"C1": current})
