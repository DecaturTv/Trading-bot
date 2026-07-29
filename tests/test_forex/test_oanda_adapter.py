import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from broker.models import OrderSide
from forex.oanda_adapter import _MAX_CANDLES_PER_REQUEST, OandaAdapter, OandaError, TradeNotSettledError

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
async def test_get_tradeable_pairs_filters_to_currency_instruments():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/accounts/acct-1/instruments"
        return httpx.Response(
            200,
            json={
                "instruments": [
                    {"name": "EUR_USD", "type": "CURRENCY", "displayPrecision": 5},
                    {"name": "XAU_USD", "type": "METAL", "displayPrecision": 3},
                    {"name": "US30_USD", "type": "CFD", "displayPrecision": 1},
                    {"name": "GBP_USD", "type": "CURRENCY", "displayPrecision": 5},
                ]
            },
        )

    adapter = make_adapter(handler)
    pairs = await adapter.get_tradeable_pairs()

    assert pairs == ["EUR_USD", "GBP_USD"]
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


def _candle(hours_after_epoch: int, complete: bool = True) -> dict:
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hours_after_epoch)
    return {
        "time": ts.isoformat().replace("+00:00", ".000000000Z"),
        "volume": 100,
        "complete": complete,
        "mid": {"o": "1.1000", "h": "1.1010", "l": "1.0990", "c": "1.1005"},
    }


@pytest.mark.asyncio
async def test_get_bars_single_page_within_range():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/instruments/EUR_USD/candles"
        assert request.url.params["count"] == "5000"
        assert "to" not in request.url.params
        calls.append(1)
        if len(calls) > 1:
            return httpx.Response(200, json={"candles": []})  # no more data past `from`
        return httpx.Response(200, json={"candles": [_candle(0), _candle(1), _candle(2, complete=False)]})

    adapter = make_adapter(handler)
    bars = await adapter.get_bars(
        "EUR_USD", "H1", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)
    )

    assert len(bars) == 2  # incomplete candle dropped
    assert bars[0].timestamp.hour == 0
    assert bars[1].timestamp.hour == 1
    await adapter.aclose()


@pytest.mark.asyncio
async def test_get_bars_pages_until_reaching_end():
    calls = []
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # end lands strictly between page 2's two candle timestamps, so exactly
    # one of them survives the `< end` filter and the loop stops right after
    # (its cursor lands past end) rather than paging a third time.
    end = epoch + timedelta(hours=_MAX_CANDLES_PER_REQUEST + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["from"])
        if len(calls) == 1:
            # A full page -- more data exists past it, must page again.
            return httpx.Response(200, json={"candles": [_candle(h) for h in range(_MAX_CANDLES_PER_REQUEST)]})
        # Second page continues right where the first left off.
        return httpx.Response(
            200,
            json={"candles": [_candle(_MAX_CANDLES_PER_REQUEST), _candle(_MAX_CANDLES_PER_REQUEST + 1)]},
        )

    adapter = make_adapter(handler)
    bars = await adapter.get_bars("EUR_USD", "H1", epoch, end)

    assert len(calls) == 2  # paged exactly once past the full first page
    assert len(bars) == _MAX_CANDLES_PER_REQUEST + 1  # the hour-5001 candle lands at/after `end`, excluded
    await adapter.aclose()


@pytest.mark.asyncio
async def test_get_bars_returns_empty_when_no_candles_available():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candles": []})

    adapter = make_adapter(handler)
    bars = await adapter.get_bars(
        "EUR_USD", "H1", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)
    )

    assert bars == []
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
    trade_id = await adapter.submit_market_order("EUR_USD", 1000, OrderSide.BUY, stop_loss_distance=0.0050, take_profit_price=1.1100)

    assert trade_id == "999"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_uses_default_precision_when_pair_unknown():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["order"] = json.loads(request.content)["order"]
        return httpx.Response(201, json={"orderFillTransaction": {"tradeOpened": {"tradeID": "1"}}})

    adapter = make_adapter(handler)
    await adapter.submit_market_order("EUR_USD", 1000, OrderSide.BUY, stop_loss_distance=0.00505, take_profit_price=1.11005)

    assert captured["order"]["trailingStopLossOnFill"]["distance"] == "0.00505"
    assert captured["order"]["takeProfitOnFill"]["price"] == "1.11005"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_uses_cached_precision_for_jpy_pairs():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/instruments"):
            return httpx.Response(
                200,
                json={"instruments": [{"name": "EUR_JPY", "type": "CURRENCY", "displayPrecision": 3}]},
            )
        captured["order"] = json.loads(request.content)["order"]
        return httpx.Response(201, json={"orderFillTransaction": {"tradeOpened": {"tradeID": "1"}}})

    adapter = make_adapter(handler)
    await adapter.get_tradeable_pairs()  # populates the precision cache
    await adapter.submit_market_order("EUR_JPY", 1000, OrderSide.BUY, stop_loss_distance=0.12345, take_profit_price=164.98765)

    assert captured["order"]["trailingStopLossOnFill"]["distance"] == "0.123"
    assert captured["order"]["takeProfitOnFill"]["price"] == "164.988"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_negates_units_for_sell():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["order"] = json.loads(request.content)["order"]
        return httpx.Response(201, json={"orderFillTransaction": {"tradeOpened": {"tradeID": "1"}}})

    adapter = make_adapter(handler)
    await adapter.submit_market_order("EUR_USD", 1000, OrderSide.SELL, stop_loss_distance=0.0050, take_profit_price=1.0900)

    assert captured["order"]["units"] == "-1000"
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_raises_when_not_filled():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"orderCancelTransaction": {"reason": "MARKET_HALTED"}})

    adapter = make_adapter(handler)

    with pytest.raises(OandaError, match="MARKET_HALTED"):
        await adapter.submit_market_order("EUR_USD", 1000, OrderSide.BUY, stop_loss_distance=0.0050, take_profit_price=1.1100)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_submit_market_order_raises_when_trailing_distance_out_of_bounds():
    # OANDA rejects a trailingStopLossOnFill distance outside the instrument's
    # min/max (e.g. tighter than minimumTrailingStopDistance) the same way it
    # rejects a halted market -- an orderCancelTransaction, no orderFillTransaction.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"orderCancelTransaction": {"reason": "TRAILING_STOP_LOSS_ON_FILL_DISTANCE_INVALID"}})

    adapter = make_adapter(handler)

    with pytest.raises(OandaError, match="TRAILING_STOP_LOSS_ON_FILL_DISTANCE_INVALID"):
        await adapter.submit_market_order("EUR_USD", 1000, OrderSide.BUY, stop_loss_distance=0.00001, take_profit_price=1.1100)
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
async def test_get_trade_realized_pnl_raises_not_settled_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errorMessage": "trade not found"})

    adapter = make_adapter(handler)

    with pytest.raises(TradeNotSettledError):
        await adapter.get_trade_realized_pnl("1")
    await adapter.aclose()


@pytest.mark.asyncio
async def test_close_trade_puts_expected_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/v3/accounts/acct-1/trades/1/close"
        return httpx.Response(200, json={"orderFillTransaction": {"tradesClosed": [{"tradeID": "1"}]}})

    adapter = make_adapter(handler)
    await adapter.close_trade("1")
    await adapter.aclose()


@pytest.mark.asyncio
async def test_close_trade_raises_when_not_filled():
    # A 200 response doesn't mean the trade actually closed -- OANDA can
    # accept the request and still cancel it (e.g. a halted market), leaving
    # the trade open. Same shape as submit_market_order's not-filled check.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"orderCancelTransaction": {"reason": "MARKET_HALTED"}})

    adapter = make_adapter(handler)

    with pytest.raises(OandaError, match="MARKET_HALTED"):
        await adapter.close_trade("1")
    await adapter.aclose()
