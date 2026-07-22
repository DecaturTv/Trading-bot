from datetime import date

import pytest
from option_factories import make_contract

from broker.models import MultiLegOrderRequest, OrderRequest, OrderSide, PositionIntent
from execution.order_builder import build_open_order_request
from options.strategy_builders import build_debit_vertical_spread, build_long_call

NEAR = date(2026, 8, 21)


def test_build_open_order_request_single_leg():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)

    request = build_open_order_request(strategy, qty=3)

    assert isinstance(request, OrderRequest)
    assert request.symbol == "C1"
    assert request.side is OrderSide.BUY
    assert request.qty == 3
    assert request.limit_price == pytest.approx(5.0)


def test_build_open_order_request_multi_leg():
    long_contract = make_contract("LONG", 100, NEAR, ask=5.0, delta=0.5)
    short_contract = make_contract("SHORT", 105, NEAR, bid=2.0, delta=0.3)
    strategy = build_debit_vertical_spread(long_contract, short_contract)

    request = build_open_order_request(strategy, qty=2)

    assert isinstance(request, MultiLegOrderRequest)
    assert request.qty == 2
    assert request.limit_price == pytest.approx(3.0)  # net_debit (300) / 100
    assert len(request.legs) == 2
    assert request.legs[0].symbol == "LONG"
    assert request.legs[0].side is OrderSide.BUY
    assert request.legs[0].position_intent is PositionIntent.BUY_TO_OPEN
    assert request.legs[1].symbol == "SHORT"
    assert request.legs[1].side is OrderSide.SELL
    assert request.legs[1].position_intent is PositionIntent.SELL_TO_OPEN


def test_build_open_order_request_rejects_non_positive_qty():
    contract = make_contract("C1", 100, NEAR, ask=5.0, delta=0.5)
    strategy = build_long_call(contract)

    with pytest.raises(ValueError):
        build_open_order_request(strategy, qty=0)
