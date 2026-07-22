import json

import httpx
import pytest

from broker.models import OrderSide
from forex.oanda_adapter import OandaAdapter, OandaError

BASE_URL = "https://api-fxpractice.oanda.com"


def make_adapter(handler) -> OandaAdapter:
    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return OandaAdapter("api-key", "acct-1", http_client=client)


@pytest.mark.asyncio
async def test_get_account_maps_oanda_summary_to_account():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/accounts/acct-1/summary"
        return httpx.Response(200, json={"account": {"NAV": "1234.56", "balance": "1000.00", "marginAvailable": "900.00", "currency": "USD"}})

    adapter = make_adapter(handler)
    account = await adapter.get_account()

    assert account.account_id == "acct-1"
    assert account.equity == 1234.56
    assert account.cash == 1000.00
    assert account.buying_power == 900.00
    assert account.currency == "USD"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_get_candles_maps_complete_candles_to_bars():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/instruments/EUR_USD/candles"
        return httpx.Response(
            200,
            json={
                "candles": [
                    {"time": "2026-07-22T10:00:00.000000000Z", "volume": 120, "complete": True,
                     "mid": {"o": "1.1000", "h": "1.1050", "l": "1.0950", "c": "1.1020"}},
                    {"time": "2026-07-22T11:00:00.000000000Z", "volume": 5, "complete": False,
                     "mid": {"o": "1.1020", "h": "1.1030", "l": "1.1010", "c": "1.1025"}},
                ]
            },
        )

    adapter = make_adapter(handler)
    bars = await adapter.get_candles("EUR_USD", count=2)

    assert len(bars) == 1  # incomplete candle dropped
    assert bars[0].symbol == "EUR_USD"
    assert bars[0].open == 1.1000
    assert bars[0].close == 1.1020
    await adapter.aclose()


@pytest.mark.asyncio
async def test_get_pricing_returns_bid_ask():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"prices": [{"instrument": "EUR_USD", "bids": [{"price": "1.1000"}], "asks": [{"price": "1.1002"}]}]},
        )

    adapter = make_adapter(handler)
    bid, ask = await adapter.get_pricing("EUR_USD")

    assert bid == 1.1000
    assert ask == 1.1002
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_returns_trade_id_on_fill():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"orderFillTransaction": {"tradeOpened": {"tradeID": "999"}}})

    adapter = make_adapter(handler)
    trade_id = await adapter.submit_market_order("EUR_USD", 1000, OrderSide.BUY, stop_loss_price=1.0950, take_profit_price=1.1100)

    assert trade_id == "999"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_negates_units_for_sell():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["order"] = json.loads(request.content)["order"]
        return httpx.Response(201, json={"orderFillTransaction": {"tradeOpened": {"tradeID": "1"}}})

    adapter = make_adapter(handler)
    await adapter.submit_market_order("EUR_USD", 1000, OrderSide.SELL, stop_loss_price=1.1050, take_profit_price=1.0900)

    assert captured["order"]["units"] == "-1000"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_raises_when_not_filled():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"orderCancelTransaction": {"reason": "MARKET_HALTED"}})

    adapter = make_adapter(handler)

    with pytest.raises(OandaError, match="MARKET_HALTED"):
        await adapter.submit_market_order("EUR_USD", 1000, OrderSide.BUY, stop_loss_price=1.0950, take_profit_price=1.1100)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_get_open_trade_ids_returns_set_of_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"trades": [{"id": "1"}, {"id": "2"}]})

    adapter = make_adapter(handler)
    ids = await adapter.get_open_trade_ids()

    assert ids == {"1", "2"}
    await adapter.aclose()


@pytest.mark.asyncio
async def test_get_trade_realized_pnl():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"trade": {"id": "1", "state": "CLOSED", "realizedPL": "-12.34"}})

    adapter = make_adapter(handler)
    pnl = await adapter.get_trade_realized_pnl("1")

    assert pnl == -12.34
    await adapter.aclose()


@pytest.mark.asyncio
async def test_close_trade_puts_expected_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/v3/accounts/acct-1/trades/1/close"
        return httpx.Response(200, json={})

    adapter = make_adapter(handler)
    await adapter.close_trade("1")
    await adapter.aclose()
