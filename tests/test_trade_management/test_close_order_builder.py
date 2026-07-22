from datetime import date

import pytest
from tm_factories import make_contract

from broker.models import MultiLegOrderRequest, OptionRight, OrderRequest, OrderSide, PositionIntent
from options.strategy_builders import build_debit_vertical_spread, build_long_call, build_long_put
from trade_management.close_order_builder import build_close_order_request

NEAR = date(2026, 8, 21)


def test_close_single_long_call_sells_at_current_bid():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)
    current = {"C1": make_contract("C1", 100, NEAR, bid=9.0, ask=11.0)}

    request = build_close_order_request(strategy, qty=2, current_contracts=current)

    assert isinstance(request, OrderRequest)
    assert request.symbol == "C1"
    assert request.side is OrderSide.SELL
    assert request.qty == 2
    assert request.limit_price == pytest.approx(9.0)


def test_close_single_long_put():
    contract = make_contract("P1", 100, NEAR, right=OptionRight.PUT, ask=4.0, delta=-0.4)
    strategy = build_long_put(contract)
    current = {"P1": make_contract("P1", 100, NEAR, bid=3.0, ask=3.5)}

    request = build_close_order_request(strategy, qty=1, current_contracts=current)

    assert request.side is OrderSide.SELL
    assert request.symbol == "P1"
    assert request.limit_price == pytest.approx(3.0)


def test_close_debit_vertical_spread_reverses_legs():
    long_contract = make_contract("LONG", 100, NEAR, ask=5.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, NEAR, bid=2.0, delta=0.3)
    strategy = build_debit_vertical_spread(long_contract, short_contract)
    current = {
        "LONG": make_contract("LONG", 100, NEAR, bid=7.0, ask=8.0),
        "SHORT": make_contract("SHORT", 105, NEAR, bid=1.0, ask=1.5),
    }

    request = build_close_order_request(strategy, qty=3, current_contracts=current)

    assert isinstance(request, MultiLegOrderRequest)
    assert request.qty == 3
    assert request.legs[0].symbol == "LONG"
    assert request.legs[0].side is OrderSide.SELL
    assert request.legs[0].position_intent is PositionIntent.SELL_TO_CLOSE
    assert request.legs[1].symbol == "SHORT"
    assert request.legs[1].side is OrderSide.BUY
    assert request.legs[1].position_intent is PositionIntent.BUY_TO_CLOSE
    # closing credit = LONG.bid - SHORT.ask = 7.0 - 1.5 = 5.5 -> limit_price = -5.5
    assert request.limit_price == pytest.approx(-5.5)


def test_close_order_rejects_non_positive_qty():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)

    with pytest.raises(ValueError):
        build_close_order_request(strategy, qty=0, current_contracts={})


def test_close_order_raises_when_current_quote_missing():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)

    with pytest.raises(ValueError):
        build_close_order_request(strategy, qty=1, current_contracts={})


def test_close_order_raises_when_needed_side_missing():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)
    current = {"C1": make_contract("C1", 100, NEAR, bid=None, ask=11.0)}

    with pytest.raises(ValueError):
        build_close_order_request(strategy, qty=1, current_contracts=current)
