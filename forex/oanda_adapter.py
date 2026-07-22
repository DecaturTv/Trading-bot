from datetime import datetime

import httpx

from broker.models import Account, Bar, OrderSide
from utils.retry import retry

_PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"
_LIVE_BASE_URL = "https://api-fxtrade.oanda.com"


class OandaError(Exception):
    pass


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
        CFD/metal instruments, which aren't forex."""
        response = await self._client.get(f"/v3/accounts/{self._account_id}/instruments")
        response.raise_for_status()
        return [i["name"] for i in response.json()["instruments"] if i["type"] == "CURRENCY"]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_candles(self, pair: str, granularity: str = "H1", count: int = 100) -> list[Bar]:
        response = await self._client.get(
            f"/v3/instruments/{pair}/candles",
            params={"granularity": granularity, "count": count, "price": "M"},
        )
        response.raise_for_status()
        candles = response.json()["candles"]
        return [
            Bar(
                symbol=pair,
                timestamp=datetime.fromisoformat(c["time"].replace("Z", "+00:00")),
                open=float(c["mid"]["o"]),
                high=float(c["mid"]["h"]),
                low=float(c["mid"]["l"]),
                close=float(c["mid"]["c"]),
                volume=float(c["volume"]),
            )
            for c in candles
            if c["complete"]
        ]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def get_pricing(self, pair: str) -> tuple[float, float]:
        """Returns (bid, ask)."""
        response = await self._client.get(f"/v3/accounts/{self._account_id}/pricing", params={"instruments": pair})
        response.raise_for_status()
        price = response.json()["prices"][0]
        return float(price["bids"][0]["price"]), float(price["asks"][0]["price"])

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def submit_market_order(
        self, pair: str, units: int, side: OrderSide, stop_loss_price: float, take_profit_price: float
    ) -> str:
        """units is always positive; side determines direction. Returns the
        opened trade's OANDA trade ID."""
        signed_units = units if side is OrderSide.BUY else -units
        order = {
            "type": "MARKET",
            "instrument": pair,
            "units": str(signed_units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{stop_loss_price:.5f}"},
            "takeProfitOnFill": {"price": f"{take_profit_price:.5f}"},
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
        response.raise_for_status()
        return float(response.json()["trade"]["realizedPL"])

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def close_trade(self, trade_id: str) -> None:
        response = await self._client.put(f"/v3/accounts/{self._account_id}/trades/{trade_id}/close")
        response.raise_for_status()
