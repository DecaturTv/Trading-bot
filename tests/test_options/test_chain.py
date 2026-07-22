from datetime import date

from factories import make_contract

from broker.models import OptionRight
from options.chain import filter_by_expiration, filter_by_right, group_by_expiration


def test_group_by_expiration_groups_correctly():
    exp1, exp2 = date(2026, 8, 21), date(2026, 9, 18)
    contracts = [
        make_contract("A", 100, exp1),
        make_contract("B", 105, exp1),
        make_contract("C", 100, exp2),
    ]

    groups = group_by_expiration(contracts)

    assert set(groups.keys()) == {exp1, exp2}
    assert {c.symbol for c in groups[exp1]} == {"A", "B"}
    assert {c.symbol for c in groups[exp2]} == {"C"}


def test_filter_by_right():
    exp = date(2026, 8, 21)
    contracts = [
        make_contract("CALL1", 100, exp, right=OptionRight.CALL),
        make_contract("PUT1", 100, exp, right=OptionRight.PUT),
    ]

    calls = filter_by_right(contracts, OptionRight.CALL)
    assert [c.symbol for c in calls] == ["CALL1"]


def test_filter_by_expiration():
    exp1, exp2 = date(2026, 8, 21), date(2026, 9, 18)
    contracts = [make_contract("A", 100, exp1), make_contract("B", 100, exp2)]

    result = filter_by_expiration(contracts, exp1)
    assert [c.symbol for c in result] == ["A"]
