from datetime import date

import pytest
from factories import make_contract

from options.selection import select_expiration, select_strike_by_delta


def test_select_expiration_picks_closest_to_target_dte():
    as_of = date(2026, 7, 1)
    expirations = [date(2026, 7, 15), date(2026, 8, 1), date(2026, 9, 1)]

    result = select_expiration(expirations, target_dte=30, as_of=as_of)

    assert result == date(2026, 8, 1)


def test_select_expiration_rejects_empty_list():
    with pytest.raises(ValueError):
        select_expiration([], target_dte=30, as_of=date(2026, 7, 1))


def test_select_strike_by_delta_picks_closest_call():
    exp = date(2026, 8, 21)
    contracts = [
        make_contract("A", 90, exp, delta=0.80),
        make_contract("B", 100, exp, delta=0.50),
        make_contract("C", 110, exp, delta=0.20),
    ]

    result = select_strike_by_delta(contracts, target_delta=0.30)
    assert result.symbol == "C"


def test_select_strike_by_delta_handles_negative_put_deltas():
    exp = date(2026, 8, 21)
    contracts = [
        make_contract("A", 90, exp, delta=-0.20),
        make_contract("B", 100, exp, delta=-0.50),
    ]

    result = select_strike_by_delta(contracts, target_delta=-0.25)
    assert result.symbol == "A"


def test_select_strike_by_delta_ignores_contracts_without_greeks():
    exp = date(2026, 8, 21)
    contracts = [make_contract("NO_GREEKS", 100, exp, delta=None), make_contract("HAS_GREEKS", 105, exp, delta=0.40)]

    result = select_strike_by_delta(contracts, target_delta=0.40)
    assert result.symbol == "HAS_GREEKS"


def test_select_strike_by_delta_rejects_when_no_candidates():
    exp = date(2026, 8, 21)
    contracts = [make_contract("NO_GREEKS", 100, exp, delta=None)]
    with pytest.raises(ValueError):
        select_strike_by_delta(contracts, target_delta=0.40)
