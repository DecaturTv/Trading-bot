import pytest

from risk.kelly import KellyResult
from risk.sizing import contracts_for_budget, position_budget_dollars


def test_position_budget_dollars():
    kelly_result = KellyResult(full_kelly_fraction=0.25, position_fraction=0.0625, used_fallback=False)
    assert position_budget_dollars(500.0, kelly_result) == pytest.approx(31.25)


def test_position_budget_dollars_rejects_negative_equity():
    kelly_result = KellyResult(full_kelly_fraction=0.0, position_fraction=0.02, used_fallback=True)
    with pytest.raises(ValueError):
        position_budget_dollars(-100.0, kelly_result)


def test_contracts_for_budget_floors_to_whole_contracts():
    assert contracts_for_budget(budget_dollars=250.0, net_debit_per_contract=100.0) == 2
    assert contracts_for_budget(budget_dollars=99.0, net_debit_per_contract=100.0) == 0


def test_contracts_for_budget_rejects_non_positive_debit():
    with pytest.raises(ValueError):
        contracts_for_budget(100.0, 0.0)
    with pytest.raises(ValueError):
        contracts_for_budget(100.0, -5.0)


def test_contracts_for_budget_rejects_negative_budget():
    with pytest.raises(ValueError):
        contracts_for_budget(-1.0, 100.0)
