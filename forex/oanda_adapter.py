from datetime import datetime, timedelta

import httpx

from broker.models import Account, Bar, OrderSide
from utils.retry import retry

_PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"
_LIVE_BASE_URL = "https://api-fxtrade.oanda.com"

# OANDA rejects count > 5000 outright ("Maximum value for 'count' exceeded"),
# not truncate -- confirmed against the practice API, not documentation.
_MAX_CANDLES_PER_REQUEST = 5000


def _candle_to_bar(pair: str, candle: dict) -> Bar:
    return Bar(
        symbol=pair,
        timestamp=datetime.fromisoformat(candle["time"].replace("Z", "+00:00")),
        open=float(candle["mid"]["o"]),
        high=float(candle["mid"]["h"]),
        low=float(candle["mid"]["l"]),
        close=float(candle["mid"]["c"]),
        volume=float(candle["volume"]),
    )


class OandaError(Exception):
    pass


class TradeNotSettledError(OandaError):
    """Trade closed on OANDA's side but its realized P&L isn't queryable yet —
    OANDA's trade endpoint lags a closed trade's data by up to a few minutes."""

    def __init__(self, trade_id: str):
        super().__init__(f"trade {trade_id} not yet settled")
        self.trade_id = trade_id


class OandaAdapter:
    """Thin wrapper over OANDA's v20 REST API — no SDK, same reasoning as the
    alert notifiers: it's a handful of HTTP calls, not worth a dependency.

    Stop-loss/take-profit/trailing-stop are attached to the order itself
    (OANDA fills and manages them server-side) rather than polled and
    evaluated locally — more reliable for a market that gaps between
    position-management cycles.
    """

    def __init__(self, api_key: str, account_id: str, live: bool = False, http_client: httpx.AsyncClient | None = None):
        self._account_id = account_id
        base_url = _LIVE_BASE_URL if live else _PRACTICE_BASE_URL
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0
        )
        # Price decimal precision varies per instrument (e.g. JPY-quoted pairs
        # use 3 decimals, not 5) — populated from OANDA's own displayPrecision
        # field so stop-loss/take-profit prices round the way OANDA expects.
        self._price_precision: dict[str, int] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_account(self) -> Account:
        response = await self._client.get(f"/v3/accounts/{self._account_id}/summary")
        response.raise_for_status()
        account = response.json()["account"]
        return Account(
            account_id=self._account_id,
            equity=float(account["NAV"]),
            cash=float(account["balance"]),
            buying_power=float(account["marginAvailable"]),
            currency=account["currency"],
        )

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_tradeable_pairs(self) -> list[str]:
        """All spot currency pairs this account can trade — excludes OANDA's
        CFD/metal instruments, which aren't forex. Also caches each pair's
        price display precision as a side effect, for submit_market_order."""
        response = await self._client.get(f"/v3/accounts/{self._account_id}/instruments")
        response.raise_for_status()
        instruments = response.json()["instruments"]
        self._price_precision.update(
            {i["name"]: int(i["displayPrecision"]) for i in instruments if "displayPrecision" in i}
        )
        return [i["name"] for i in instruments if i["type"] == "CURRENCY"]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_candles(self, pair: str, granularity: str = "H1", count: int = 100) -> list[Bar]:
        response = await self._client.get(
            f"/v3/instruments/{pair}/candles",
            params={"granularity": granularity, "count": count, "price": "M"},
        )
        response.raise_for_status()
        candles = response.json()["candles"]
        return [_candle_to_bar(pair, c) for c in candles if c["complete"]]

    async def get_bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        """Historical date-range candle fetch for backtesting -- unlike
        get_candles (live, most-recent-N), this pages through OANDA's
        5000-candles-per-request cap (confirmed empirically: passing `to` far
        enough past `from` to imply more than 5000 candles gets a 400, not a
        truncated response) using `from`+count=5000 per page, each page
        picking up where the previous one's last candle left off.

        timeframe is passed straight through as OANDA's own granularity code
        (e.g. "H1"), not translated like Alpaca's get_bars. Duck-typed to
        BrokerAdapter's get_bars signature so BarIngestionService can backfill
        forex history the same way it does equities, even though OandaAdapter
        deliberately isn't a BrokerAdapter (see broker/base.py -- that's about
        margin/leverage trading mechanics, which don't apply to plain
        historical bars).
        """
        bars: list[Bar] = []
        cursor = start
        while cursor < end:
            page = await self._fetch_candles_page(symbol, timeframe, cursor)
            if not page:
                break
            bars.extend(b for b in page if b.timestamp < end)
            last_ts = page[-1].timestamp
            if last_ts <= cursor:
                break  # no forward progress -- avoid spinning forever
            cursor = last_ts + timedelta(seconds=1)
        return bars

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def _fetch_candles_page(self, pair: str, granularity: str, from_: datetime) -> list[Bar]:
        # Retried per-page (not around the whole get_bars loop) so a transient
        # failure partway through a large backfill doesn't discard pages
        # already fetched and restart from the beginning.
        response = await self._client.get(
            f"/v3/instruments/{pair}/candles",
            params={"granularity": granularity, "from": from_.isoformat().replace("+00:00", "Z"),
                     "count": _MAX_CANDLES_PER_REQUEST, "price": "M"},
        )
        response.raise_for_status()
        return [_candle_to_bar(pair, c) for c in response.json()["candles"] if c["complete"]]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_pricing(self, pair: str) -> tuple[float, float]:
        """Returns (bid, ask)."""
        response = await self._client.get(f"/v3/accounts/{self._account_id}/pricing", params={"instruments": pair})
        response.raise_for_status()
        price = response.json()["prices"][0]
        return float(price["bids"][0]["price"]), float(price["asks"][0]["price"])

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def submit_market_order(
        self, pair: str, units: int, side: OrderSide, stop_loss_distance: float, take_profit_price: float
    ) -> str:
        """units is always positive; side determines direction. Returns the
        opened trade's OANDA trade ID.

        stop_loss_distance is a trailing stop (trailingStopLossOnFill), not a
        fixed price -- OANDA ratchets it forward as the trade moves favorably
        and never lets it loosen, entirely server-side, so it needs no local
        polling/repricing (same reasoning as attaching stopLoss/takeProfit at
        entry in the first place: reliable across gaps between position-
        management cycles). Note OANDA enforces a per-instrument min/max
        trailing distance (instruments endpoint's minimumTrailingStopDistance/
        maximumTrailingStopDistance) -- a distance outside that range gets the
        order cancelled, surfaced below as the usual "not filled" OandaError.
        """
        signed_units = units if side is OrderSide.BUY else -units
        precision = self._price_precision.get(pair, 5)
        order = {
            "type": "MARKET",
            "instrument": pair,
            "units": str(signed_units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "trailingStopLossOnFill": {"distance": f"{stop_loss_distance:.{precision}f}"},
            "takeProfitOnFill": {"price": f"{take_profit_price:.{precision}f}"},
        }
        response = await self._client.post(f"/v3/accounts/{self._account_id}/orders", json={"order": order})
        response.raise_for_status()
        body = response.json()
        fill = body.get("orderFillTransaction")
        if fill is None or "tradeOpened" not in fill:
            reason = body.get("orderCancelTransaction", {}).get("reason", "unknown")
            raise OandaError(f"order for {pair} was not filled: {reason}")
        return fill["tradeOpened"]["tradeID"]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_open_trade_ids(self) -> set[str]:
        response = await self._client.get(f"/v3/accounts/{self._account_id}/openTrades")
        response.raise_for_status()
        return {t["id"] for t in response.json()["trades"]}

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_trade_realized_pnl(self, trade_id: str) -> float:
        response = await self._client.get(f"/v3/accounts/{self._account_id}/trades/{trade_id}")
        if response.status_code == 404:
            raise TradeNotSettledError(trade_id)
        response.raise_for_status()
        return float(response.json()["trade"]["realizedPL"])

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def close_trade(self, trade_id: str) -> None:
        """Raises OandaError if the close didn't actually fill (e.g. OANDA
        cancels it with reason MARKET_HALTED) -- a 200 response here just
        means the request was accepted, not that the trade closed."""
        response = await self._client.put(f"/v3/accounts/{self._account_id}/trades/{trade_id}/close")
        response.raise_for_status()
        body = response.json()
        if body.get("orderFillTransaction") is None:
            reason = body.get("orderCancelTransaction", {}).get("reason", "unknown")
            raise OandaError(f"close of trade {trade_id} was not filled: {reason}")
