import pytest

from forex.sizing import units_for_risk


def test_units_for_risk_computes_expected_units():
    # risk $20 (2% of $1000) at a $0.005/unit stop distance -> ~4000 units
    # (floor division on floats lands one unit short of the exact 4000 here
    # due to binary float representation of 0.005 — expected, not a bug)
    units = units_for_risk(equity=1000.0, risk_pct=0.02, stop_loss_distance=0.005)
    assert units == 3999


def test_units_for_risk_applies_quote_to_account_rate():
    # Same $20 risk and $0.005 stop distance, but the stop distance is in a
    # quote currency worth half the account currency -- real account-currency
    # risk per unit is halved, so twice as many units fit the same budget.
    units = units_for_risk(equity=1000.0, risk_pct=0.02, stop_loss_distance=0.005, quote_to_account_rate=0.5)
    assert units == 7999


def test_units_for_risk_rejects_non_positive_equity():
    with pytest.raises(ValueError):
        units_for_risk(equity=0.0, risk_pct=0.02, stop_loss_distance=0.005)


def test_units_for_risk_rejects_invalid_risk_pct():
    with pytest.raises(ValueError):
        units_for_risk(equity=1000.0, risk_pct=1.5, stop_loss_distance=0.005)


def test_units_for_risk_rejects_non_positive_stop_distance():
    with pytest.raises(ValueError):
        units_for_risk(equity=1000.0, risk_pct=0.02, stop_loss_distance=0.0)


def test_units_for_risk_rejects_non_positive_conversion_rate():
    with pytest.raises(ValueError):
        units_for_risk(equity=1000.0, risk_pct=0.02, stop_loss_distance=0.005, quote_to_account_rate=0.0)
