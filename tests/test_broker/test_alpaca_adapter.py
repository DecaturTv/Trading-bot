from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import OrderStatus as AlpacaOrderStatus
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import PositionSide

from broker.alpaca_adapter import AlpacaAdapter
from broker.exceptions import BrokerError
from broker.models import OrderRequest
from broker.models import OrderSide as Side
from broker.models import OrderStatus, OrderType
from broker.models import TimeInForce as TIF


def make_adapter():
    trading_client = MagicMock()
    data_client = MagicMock()
    adapter = AlpacaAdapter(
        trading_client=trading_client,
        data_client=data_client,
        screener_client=MagicMock(),
        option_data_client=MagicMock(),
    )
    return adapter, trading_client, data_client


@pytest.mark.asyncio
async def test_get_account_maps_fields():
    adapter, trading_client, _ = make_adapter()
    trading_client.get_account.return_value = SimpleNamespace(
        id="acct-1", equity="1234.56", cash="1000.00", buying_power="2000.00", currency="USD"
    )

    account = await adapter.get_account()

    assert account.account_id == "acct-1"
    assert account.equity == 1234.56
    assert account.cash == 1000.0
    assert account.buying_power == 2000.0
    assert account.currency == "USD"


@pytest.mark.asyncio
async def test_get_positions_maps_side_from_position_side():
    adapter, trading_client, _ = make_adapter()
    trading_client.get_all_positions.return_value = [
        SimpleNamespace(
            symbol="AAPL",
            qty="10",
            side=PositionSide.LONG,
            avg_entry_price="150.00",
            market_value="1550.00",
            unrealized_pl="50.00",
        ),
        SimpleNamespace(
            symbol="TSLA",
            qty="5",
            side=PositionSide.SHORT,
            avg_entry_price="200.00",
            market_value="-1000.00",
            unrealized_pl="-25.00",
        ),
    ]

    positions = await adapter.get_positions()

    assert positions[0].symbol == "AAPL"
    assert positions[0].side is Side.BUY
    assert positions[1].side is Side.SELL


@pytest.mark.asyncio
async def test_get_position_returns_none_on_404():
    adapter, trading_client, _ = make_adapter()
    error = APIError("position does not exist")
    error._http_error = SimpleNamespace(response=SimpleNamespace(status_code=404))
    trading_client.get_open_position.side_effect = error

    position = await adapter.get_position("AAPL")

    assert position is None


@pytest.mark.asyncio
async def test_get_position_raises_on_non_404_error():
    adapter, trading_client, _ = make_adapter()
    error = APIError("internal server error")
    error._http_error = SimpleNamespace(response=SimpleNamespace(status_code=500))
    trading_client.get_open_position.side_effect = error

    with pytest.raises(BrokerError):
        await adapter.get_position("AAPL")


@pytest.mark.asyncio
async def test_get_latest_quote_maps_fields():
    adapter, _, data_client = make_adapter()
    now = datetime.now(timezone.utc)
    data_client.get_stock_latest_quote.return_value = {
        "AAPL": SimpleNamespace(bid_price=150.0, ask_price=150.5, bid_size=100, ask_size=200, timestamp=now)
    }

    quote = await adapter.get_latest_quote("AAPL")

    assert quote.symbol == "AAPL"
    assert quote.bid_price == 150.0
    assert quote.ask_price == 150.5
    assert quote.timestamp == now


@pytest.mark.asyncio
async def test_get_bars_maps_list():
    adapter, _, data_client = make_adapter()
    now = datetime.now(timezone.utc)
    data_client.get_stock_bars.return_value = {
        "AAPL": [
            SimpleNamespace(timestamp=now, open=150, high=152, low=149, close=151, volume=1_000_000),
        ]
    }

    bars = await adapter.get_bars("AAPL", "1Day", now, now)

    assert len(bars) == 1
    assert bars[0].close == 151


def test_invalid_timeframe_raises():
    from broker.alpaca_adapter import _parse_timeframe

    with pytest.raises(ValueError, match="invalid timeframe"):
        _parse_timeframe("banana")


@pytest.mark.asyncio
async def test_submit_order_builds_market_order_and_maps_response():
    adapter, trading_client, _ = make_adapter()
    now = datetime.now(timezone.utc)
    trading_client.submit_order.return_value = SimpleNamespace(
        id="order-1",
        symbol="AAPL",
        qty="10",
        side=AlpacaOrderSide.BUY,
        type=AlpacaOrderType.MARKET,
        status=AlpacaOrderStatus.NEW,
        filled_qty="0",
        filled_avg_price=None,
        submitted_at=now,
        filled_at=None,
    )

    order = await adapter.submit_order(
        OrderRequest(symbol="AAPL", qty=10, side=Side.BUY, order_type=OrderType.MARKET, time_in_force=TIF.DAY)
    )

    assert order.order_id == "order-1"
    assert order.status is OrderStatus.NEW
    assert order.filled_avg_price is None
    trading_client.submit_order.assert_called_once()


@pytest.mark.asyncio
async def test_submit_order_is_not_retried_on_failure():
    adapter, trading_client, _ = make_adapter()
    trading_client.submit_order.side_effect = APIError("insufficient buying power")

    with pytest.raises(BrokerError):
        await adapter.submit_order(
            OrderRequest(symbol="AAPL", qty=10, side=Side.BUY, order_type=OrderType.MARKET, time_in_force=TIF.DAY)
        )

    assert trading_client.submit_order.call_count == 1


@pytest.mark.asyncio
async def test_get_account_retries_then_succeeds(monkeypatch):
    async def no_sleep(_):
        return None

    import asyncio as asyncio_module

    monkeypatch.setattr(asyncio_module, "sleep", no_sleep)

    adapter, trading_client, _ = make_adapter()
    trading_client.get_account.side_effect = [
        APIError("transient"),
        SimpleNamespace(id="acct-1", equity="100", cash="100", buying_power="100", currency="USD"),
    ]

    account = await adapter.get_account()

    assert account.account_id == "acct-1"
    assert trading_client.get_account.call_count == 2


@pytest.mark.asyncio
async def test_get_most_active_symbols_maps_fields():
    screener_client = MagicMock()
    adapter = AlpacaAdapter(
        trading_client=MagicMock(),
        data_client=MagicMock(),
        screener_client=screener_client,
        option_data_client=MagicMock(),
    )
    screener_client.get_most_actives.return_value = SimpleNamespace(
        most_actives=[
            SimpleNamespace(symbol="AAPL", volume=1_000_000.0, trade_count=5000.0),
            SimpleNamespace(symbol="TSLA", volume=800_000.0, trade_count=4000.0),
        ],
        last_updated=datetime.now(timezone.utc),
    )

    result = await adapter.get_most_active_symbols(top=20)

    assert [a.symbol for a in result] == ["AAPL", "TSLA"]
    assert result[0].volume == 1_000_000.0
    screener_client.get_most_actives.assert_called_once()


def make_alpaca_contract(symbol, strike, expiration, right):
    return SimpleNamespace(
        symbol=symbol,
        underlying_symbol="AAPL",
        strike_price=strike,
        expiration_date=expiration,
        type=right,
    )


@pytest.mark.asyncio
async def test_get_option_chain_merges_contracts_with_snapshots():
    from datetime import date

    from alpaca.trading.enums import ContractType

    trading_client = MagicMock()
    option_data_client = MagicMock()
    adapter = AlpacaAdapter(
        trading_client=trading_client,
        data_client=MagicMock(),
        screener_client=MagicMock(),
        option_data_client=option_data_client,
    )
    expiration = date(2026, 8, 21)

    trading_client.get_option_contracts.return_value = SimpleNamespace(
        option_contracts=[
            make_alpaca_contract("AAPL260821C00150000", 150.0, expiration, ContractType.CALL),
            make_alpaca_contract("AAPL260821P00150000", 150.0, expiration, ContractType.PUT),
        ],
        next_page_token=None,
    )
    option_data_client.get_option_chain.return_value = {
        "AAPL260821C00150000": SimpleNamespace(
            latest_quote=SimpleNamespace(bid_price=5.0, ask_price=5.2),
            latest_trade=SimpleNamespace(price=5.1),
            implied_volatility=0.35,
            greeks=SimpleNamespace(delta=0.5, gamma=0.02, theta=-0.05, vega=0.1, rho=0.01),
        )
        # no snapshot for the put contract, to exercise the "missing pricing data" path
    }

    result = await adapter.get_option_chain("AAPL", expiration_gte=expiration, expiration_lte=expiration)

    assert len(result) == 2
    call = next(c for c in result if c.symbol == "AAPL260821C00150000")
    put = next(c for c in result if c.symbol == "AAPL260821P00150000")

    assert call.right.value == "call"
    assert call.bid == 5.0
    assert call.ask == 5.2
    assert call.implied_volatility == 0.35
    assert call.greeks.delta == 0.5

    assert put.right.value == "put"
    assert put.bid is None
    assert put.greeks is None


@pytest.mark.asyncio
async def test_get_option_chain_paginates_contract_fetch():
    from datetime import date

    from alpaca.trading.enums import ContractType

    trading_client = MagicMock()
    option_data_client = MagicMock()
    adapter = AlpacaAdapter(
        trading_client=trading_client,
        data_client=MagicMock(),
        screener_client=MagicMock(),
        option_data_client=option_data_client,
    )
    expiration = date(2026, 8, 21)

    trading_client.get_option_contracts.side_effect = [
        SimpleNamespace(
            option_contracts=[make_alpaca_contract("A1", 100.0, expiration, ContractType.CALL)],
            next_page_token="page-2",
        ),
        SimpleNamespace(
            option_contracts=[make_alpaca_contract("A2", 105.0, expiration, ContractType.CALL)],
            next_page_token=None,
        ),
    ]
    option_data_client.get_option_chain.return_value = {}

    result = await adapter.get_option_chain("AAPL")

    assert {c.symbol for c in result} == {"A1", "A2"}
    assert trading_client.get_option_contracts.call_count == 2


@pytest.mark.asyncio
async def test_list_orders_filters_to_exact_status():
    adapter, trading_client, _ = make_adapter()
    now = datetime.now(timezone.utc)

    def make_order(order_id, status):
        return SimpleNamespace(
            id=order_id,
            symbol="AAPL",
            qty="1",
            side=AlpacaOrderSide.BUY,
            type=AlpacaOrderType.MARKET,
            status=AlpacaOrderStatus(status),
            filled_qty="0",
            filled_avg_price=None,
            submitted_at=now,
            filled_at=None,
        )

    trading_client.get_orders.return_value = [
        make_order("o1", "filled"),
        make_order("o2", "canceled"),
    ]

    orders = await adapter.list_orders(status=OrderStatus.FILLED)

    assert [o.order_id for o in orders] == ["o1"]
