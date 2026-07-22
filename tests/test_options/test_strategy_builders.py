from datetime import date

import pytest
from factories import make_contract

from broker.models import OptionRight
from options.models import StrategyConstructionError, StrategyType
from options.strategy_builders import (
    build_debit_diagonal_spread,
    build_debit_vertical_spread,
    build_long_call,
    build_long_put,
)

NEAR = date(2026, 8, 21)
FAR = date(2026, 9, 18)


def test_build_long_call():
    contract = make_contract("C1", 100, NEAR, right=OptionRight.CALL, ask=5.0, delta=0.5)

    strategy = build_long_call(contract)

    assert strategy.strategy_type is StrategyType.LONG_CALL
    assert strategy.net_debit == pytest.approx(500.0)
    assert strategy.max_loss == pytest.approx(500.0)
    assert strategy.max_gain is None
    assert strategy.net_delta == pytest.approx(0.5)


def test_build_long_call_rejects_put_contract():
    contract = make_contract("P1", 100, NEAR, right=OptionRight.PUT, ask=5.0, delta=-0.5)
    with pytest.raises(StrategyConstructionError):
        build_long_call(contract)


def test_build_long_put():
    contract = make_contract("P1", 100, NEAR, right=OptionRight.PUT, ask=4.0, delta=-0.4)

    strategy = build_long_put(contract)

    assert strategy.strategy_type is StrategyType.LONG_PUT
    assert strategy.net_debit == pytest.approx(400.0)
    assert strategy.max_loss == pytest.approx(400.0)
    assert strategy.max_gain == pytest.approx(100 * 100 - 400.0)
    assert strategy.net_delta == pytest.approx(-0.4)


def test_build_long_option_rejects_missing_ask():
    contract = make_contract("C1", 100, NEAR, right=OptionRight.CALL, ask=None, delta=0.5)
    with pytest.raises(StrategyConstructionError):
        build_long_call(contract)


def test_build_debit_vertical_spread():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=5.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, NEAR, right=OptionRight.CALL, bid=2.0, delta=0.3)

    strategy = build_debit_vertical_spread(long_contract, short_contract)

    assert strategy.strategy_type is StrategyType.DEBIT_SPREAD_VERTICAL
    assert strategy.net_debit == pytest.approx(300.0)
    assert strategy.max_loss == pytest.approx(300.0)
    assert strategy.max_gain == pytest.approx(500.0 - 300.0)
    assert strategy.net_delta == pytest.approx(0.2)


def test_build_debit_vertical_spread_rejects_mismatched_right():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=5.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, NEAR, right=OptionRight.PUT, bid=2.0, delta=-0.3)
    with pytest.raises(StrategyConstructionError):
        build_debit_vertical_spread(long_contract, short_contract)


def test_build_debit_vertical_spread_rejects_mismatched_expiration():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=5.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, FAR, right=OptionRight.CALL, bid=2.0, delta=0.3)
    with pytest.raises(StrategyConstructionError):
        build_debit_vertical_spread(long_contract, short_contract)


def test_build_debit_vertical_spread_rejects_same_strike():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=5.0, delta=0.5)
    short_contract = make_contract("SHORT", 100, NEAR, right=OptionRight.CALL, bid=2.0, delta=0.3)
    with pytest.raises(StrategyConstructionError):
        build_debit_vertical_spread(long_contract, short_contract)


def test_build_debit_vertical_spread_rejects_net_credit():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=2.0, delta=0.3)
    short_contract = make_contract("SHORT", 105, NEAR, right=OptionRight.CALL, bid=5.0, delta=0.5)
    with pytest.raises(StrategyConstructionError, match="net credit"):
        build_debit_vertical_spread(long_contract, short_contract)


def test_build_debit_diagonal_spread():
    long_contract = make_contract("LONG", 100, FAR, right=OptionRight.CALL, ask=6.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, NEAR, right=OptionRight.CALL, bid=2.5, delta=0.35)

    strategy = build_debit_diagonal_spread(long_contract, short_contract)

    assert strategy.strategy_type is StrategyType.DEBIT_SPREAD_DIAGONAL
    assert strategy.net_debit == pytest.approx(350.0)
    assert strategy.max_loss == pytest.approx(350.0)
    assert strategy.max_gain is None
    assert strategy.net_delta == pytest.approx(0.15)


def test_build_debit_diagonal_spread_rejects_wrong_expiration_order():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=6.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, FAR, right=OptionRight.CALL, bid=2.5, delta=0.35)
    with pytest.raises(StrategyConstructionError):
        build_debit_diagonal_spread(long_contract, short_contract)


def test_build_debit_diagonal_spread_rejects_same_expiration():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=6.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, NEAR, right=OptionRight.CALL, bid=2.5, delta=0.35)
    with pytest.raises(StrategyConstructionError):
        build_debit_diagonal_spread(long_contract, short_contract)


def test_build_debit_diagonal_spread_rejects_net_credit():
    long_contract = make_contract("LONG", 100, FAR, right=OptionRight.CALL, ask=2.0, delta=0.3)
    short_contract = make_contract("SHORT", 105, NEAR, right=OptionRight.CALL, bid=5.0, delta=0.5)
    with pytest.raises(StrategyConstructionError, match="net credit"):
        build_debit_diagonal_spread(long_contract, short_contract)


def test_build_debit_vertical_spread_rejects_missing_delta():
    long_contract = make_contract("LONG", 100, NEAR, right=OptionRight.CALL, ask=5.0, delta=None)
    short_contract = make_contract("SHORT", 105, NEAR, right=OptionRight.CALL, bid=2.0, delta=0.3)
    with pytest.raises(StrategyConstructionError):
        build_debit_vertical_spread(long_contract, short_contract)
