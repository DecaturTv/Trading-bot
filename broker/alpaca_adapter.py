import asyncio
import re
from datetime import date, datetime

from alpaca.common.exceptions import APIError
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.enums import DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    MostActivesRequest,
    OptionChainRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus
from alpaca.trading.enums import ContractType as AlpacaContractType
from alpaca.trading.enums import OrderClass as AlpacaOrderClass
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import PositionIntent as AlpacaPositionIntent
from alpaca.trading.enums import PositionSide
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import (
    GetOptionContractsRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from config.settings import Settings
from utils.retry import CircuitBreaker, retry

from .base import BrokerAdapter
from .exceptions import BrokerError
from .models import (
    Account,
    ActiveSymbol,
    Bar,
    MultiLegOrderRequest,
    OptionContract,
    OptionGreeks,
    OptionRight,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    PositionIntent,
)
from .models import OrderSide as Side
from .models import Quote
from .models import TimeInForce as TIF

_SIDE_TO_ALPACA = {Side.BUY: AlpacaOrderSide.BUY, Side.SELL: AlpacaOrderSide.SELL}
_SIDE_FROM_ALPACA = {v: k for k, v in _SIDE_TO_ALPACA.items()}
_SIDE_FROM_POSITION = {PositionSide.LONG: Side.BUY, PositionSide.SHORT: Side.SELL}

_POSITION_INTENT_TO_ALPACA = {
    PositionIntent.BUY_TO_OPEN: AlpacaPositionIntent.BUY_TO_OPEN,
    PositionIntent.BUY_TO_CLOSE: AlpacaPositionIntent.BUY_TO_CLOSE,
    PositionIntent.SELL_TO_OPEN: AlpacaPositionIntent.SELL_TO_OPEN,
    PositionIntent.SELL_TO_CLOSE: AlpacaPositionIntent.SELL_TO_CLOSE,
}

_TIF_TO_ALPACA = {
    TIF.DAY: AlpacaTimeInForce.DAY,
    TIF.GTC: AlpacaTimeInForce.GTC,
    TIF.IOC: AlpacaTimeInForce.IOC,
    TIF.FOK: AlpacaTimeInForce.FOK,
}

_STATUS_FROM_ALPACA = {
    "new": OrderStatus.NEW,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED,
    "pending_cancel": OrderStatus.PENDING_CANCEL,
    "expired": OrderStatus.EXPIRED,
}

_TIMEFRAME_UNIT = {
    "Min": TimeFrameUnit.Minute,
    "Hour": TimeFrameUnit.Hour,
    "Day": TimeFrameUnit.Day,
    "Week": TimeFrameUnit.Week,
    "Month": TimeFrameUnit.Month,
}
_TIMEFRAME_RE = re.compile(r"(\d+)(Min|Hour|Day|Week|Month)")

_OPEN_STATUSES = {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL}


def _parse_timeframe(timeframe: str) -> TimeFrame:
    match = _TIMEFRAME_RE.fullmatch(timeframe)
    if not match:
        raise ValueError(f"invalid timeframe: {timeframe!r} (expected e.g. '1Min', '15Min', '1Day')")
    amount, unit = match.groups()
    return TimeFrame(int(amount), _TIMEFRAME_UNIT[unit])


def _map_account(raw) -> Account:
    return Account(
        account_id=str(raw.id),
        equity=float(raw.equity),
        cash=float(raw.cash),
        buying_power=float(raw.buying_power),
        currency=raw.currency,
    )


def _map_position(raw) -> Position:
    return Position(
        symbol=raw.symbol,
        qty=float(raw.qty),
        side=_SIDE_FROM_POSITION[raw.side],
        avg_entry_price=float(raw.avg_entry_price),
        market_value=float(raw.market_value),
        unrealized_pl=float(raw.unrealized_pl),
    )


def _map_quote(symbol: str, raw) -> Quote:
    return Quote(
        symbol=symbol,
        bid_price=float(raw.bid_price),
        ask_price=float(raw.ask_price),
        bid_size=float(raw.bid_size),
        ask_size=float(raw.ask_size),
        timestamp=raw.timestamp,
    )


def _map_bar(symbol: str, raw) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=raw.timestamp,
        open=float(raw.open),
        high=float(raw.high),
        low=float(raw.low),
        close=float(raw.close),
        volume=float(raw.volume),
    )


def _map_order(raw) -> Order:
    # Multi-leg combo orders have no single top-level symbol/side/type in
    # Alpaca's response (each leg carries its own) — fall back sensibly so
    # the combo order still maps, while each nested leg maps with its real
    # per-leg values via this same function.
    status = _STATUS_FROM_ALPACA.get(
        raw.status.value if hasattr(raw.status, "value") else str(raw.status), OrderStatus.OTHER
    )
    legs = [_map_order(leg) for leg in raw.legs] if getattr(raw, "legs", None) else None
    symbol = raw.symbol or (legs[0].symbol if legs else "")
    side = _SIDE_FROM_ALPACA.get(raw.side, Side.BUY) if raw.side is not None else Side.BUY
    order_type = (
        OrderType(raw.type.value if hasattr(raw.type, "value") else str(raw.type))
        if raw.type is not None
        else OrderType.LIMIT
    )
    return Order(
        order_id=str(raw.id),
        symbol=symbol,
        qty=float(raw.qty),
        side=side,
        order_type=order_type,
        status=status,
        filled_qty=float(raw.filled_qty or 0),
        filled_avg_price=float(raw.filled_avg_price) if raw.filled_avg_price else None,
        submitted_at=raw.submitted_at,
        filled_at=raw.filled_at,
        legs=legs,
    )


def _map_active_symbol(raw) -> ActiveSymbol:
    return ActiveSymbol(symbol=raw.symbol, volume=float(raw.volume))


def _map_option_greeks(raw) -> OptionGreeks:
    return OptionGreeks(delta=raw.delta, gamma=raw.gamma, theta=raw.theta, vega=raw.vega, rho=raw.rho)


def _map_option_contract(contract, snapshot) -> OptionContract:
    quote = snapshot.latest_quote if snapshot else None
    trade = snapshot.latest_trade if snapshot else None
    return OptionContract(
        symbol=contract.symbol,
        underlying_symbol=contract.underlying_symbol,
        strike=float(contract.strike_price),
        expiration=contract.expiration_date,
        right=OptionRight.CALL if contract.type == AlpacaContractType.CALL else OptionRight.PUT,
        bid=quote.bid_price if quote else None,
        ask=quote.ask_price if quote else None,
        last_price=trade.price if trade else None,
        implied_volatility=snapshot.implied_volatility if snapshot else None,
        greeks=_map_option_greeks(snapshot.greeks) if snapshot and snapshot.greeks else None,
    )


def _build_alpaca_order_request(order: OrderRequest):
    kwargs = dict(
        symbol=order.symbol,
        qty=order.qty,
        side=_SIDE_TO_ALPACA[order.side],
        time_in_force=_TIF_TO_ALPACA[order.time_in_force],
    )
    if order.order_type is OrderType.MARKET:
        return MarketOrderRequest(**kwargs)
    if order.order_type is OrderType.LIMIT:
        return LimitOrderRequest(limit_price=order.limit_price, **kwargs)
    if order.order_type is OrderType.STOP:
        return StopOrderRequest(stop_price=order.stop_price, **kwargs)
    if order.order_type is OrderType.STOP_LIMIT:
        return StopLimitOrderRequest(limit_price=order.limit_price, stop_price=order.stop_price, **kwargs)
    raise ValueError(f"unsupported order type: {order.order_type}")


class AlpacaAdapter(BrokerAdapter):
    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
        trading_client: TradingClient | None = None,
        data_client: StockHistoricalDataClient | None = None,
        screener_client: ScreenerClient | None = None,
        option_data_client: OptionHistoricalDataClient | None = None,
    ):
        self._trading_client = trading_client or TradingClient(api_key, secret_key, paper=paper)
        self._data_client = data_client or StockHistoricalDataClient(api_key, secret_key)
        self._screener_client = screener_client or ScreenerClient(api_key, secret_key)
        self._option_data_client = option_data_client or OptionHistoricalDataClient(api_key, secret_key)
        self._breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

    @classmethod
    def from_settings(cls, settings: Settings) -> "AlpacaAdapter":
        if not settings.alpaca_api_key or not settings.alpaca_secret_key:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
        # Alpaca is the paper-trading broker for this project (Schwab handles live) — see project memory.
        return cls(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key, paper=True)

    async def _call(self, func, *args, **kwargs):
        try:
            return await self._breaker.call_async(asyncio.to_thread, func, *args, **kwargs)
        except APIError as exc:
            raise BrokerError(str(exc), status_code=exc.status_code) from exc

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def get_account(self) -> Account:
        raw = await self._call(self._trading_client.get_account)
        return _map_account(raw)

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def get_positions(self) -> list[Position]:
        raw = await self._call(self._trading_client.get_all_positions)
        return [_map_position(p) for p in raw]

    async def get_position(self, symbol: str) -> Position | None:
        # Only a 404 means "no position" — anything else (timeout, 5xx) must
        # surface as an error, not be conflated with "flat", or risk/ could
        # miss an existing position after a transient broker outage.
        try:
            raw = await self._call(self._trading_client.get_open_position, symbol)
        except BrokerError as exc:
            if exc.status_code == 404:
                return None
            raise
        return _map_position(raw)

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def get_latest_quote(self, symbol: str) -> Quote:
        # feed=IEX: the SIP feed 403s on recent data without Alpaca's paid
        # Algo Trader Plus data plan; IEX is what the free/basic plan grants.
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        raw = await self._call(self._data_client.get_stock_latest_quote, request)
        return _map_quote(symbol, raw[symbol])

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def get_bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=_parse_timeframe(timeframe),
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        raw = await self._call(self._data_client.get_stock_bars, request)
        return [_map_bar(symbol, b) for b in raw[symbol]]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def get_most_active_symbols(self, top: int = 20) -> list[ActiveSymbol]:
        request = MostActivesRequest(top=top, by="volume")
        raw = await self._call(self._screener_client.get_most_actives, request)
        return [_map_active_symbol(a) for a in raw.most_actives]

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def get_option_chain(
        self,
        underlying_symbol: str,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
    ) -> list[OptionContract]:
        # Alpaca splits option data across two endpoints: contract reference
        # data (strike/expiration/type) from the trading API, and pricing/IV/
        # greeks from the market data API — merge them by contract symbol.
        contracts = await self._fetch_option_contracts(underlying_symbol, expiration_gte, expiration_lte)
        chain_request = OptionChainRequest(
            underlying_symbol=underlying_symbol,
            expiration_date_gte=expiration_gte,
            expiration_date_lte=expiration_lte,
        )
        snapshots = await self._call(self._option_data_client.get_option_chain, chain_request)
        return [_map_option_contract(c, snapshots.get(c.symbol)) for c in contracts]

    async def _fetch_option_contracts(
        self, underlying_symbol: str, expiration_gte: date | None, expiration_lte: date | None
    ):
        contracts = []
        page_token = None
        for _ in range(20):  # safety cap: a single underlying's chain fits well within this
            request = GetOptionContractsRequest(
                underlying_symbols=[underlying_symbol],
                status=AssetStatus.ACTIVE,
                expiration_date_gte=expiration_gte,
                expiration_date_lte=expiration_lte,
                page_token=page_token,
            )
            response = await self._call(self._trading_client.get_option_contracts, request)
            contracts.extend(response.option_contracts)
            page_token = response.next_page_token
            if not page_token:
                break
        return contracts

    async def submit_order(self, order: OrderRequest) -> Order:
        # Not retried: resubmitting a failed order risks a duplicate fill.
        # execution/ decides whether/how to retry based on order + account state.
        request = _build_alpaca_order_request(order)
        raw = await self._call(self._trading_client.submit_order, request)
        return _map_order(raw)

    async def submit_multi_leg_order(self, order: MultiLegOrderRequest) -> Order:
        # Not retried, same reasoning as submit_order. Combo orders fill all
        # legs atomically as a unit and only support a net limit price on
        # Alpaca — no market-order combo, to avoid an unpredictable net fill
        # price across legs.
        request = LimitOrderRequest(
            qty=order.qty,
            time_in_force=_TIF_TO_ALPACA[order.time_in_force],
            order_class=AlpacaOrderClass.MLEG,
            limit_price=order.limit_price,
            legs=[
                OptionLegRequest(
                    symbol=leg.symbol,
                    ratio_qty=leg.ratio_qty,
                    side=_SIDE_TO_ALPACA[leg.side],
                    position_intent=_POSITION_INTENT_TO_ALPACA[leg.position_intent],
                )
                for leg in order.legs
            ],
        )
        raw = await self._call(self._trading_client.submit_order, request)
        return _map_order(raw)

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def cancel_order(self, order_id: str) -> None:
        await self._call(self._trading_client.cancel_order_by_id, order_id)

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def get_order(self, order_id: str) -> Order:
        raw = await self._call(self._trading_client.get_order_by_id, order_id)
        return _map_order(raw)

    @retry(max_attempts=3, base_delay=0.5, exceptions=(BrokerError,))
    async def list_orders(self, status: OrderStatus | None = None) -> list[Order]:
        # Alpaca only filters by open/closed/all server-side; narrow to the
        # exact requested status client-side after fetching.
        query_status = QueryOrderStatus.ALL
        if status is not None:
            query_status = QueryOrderStatus.OPEN if status in _OPEN_STATUSES else QueryOrderStatus.CLOSED
        request = GetOrdersRequest(status=query_status)
        raw = await self._call(self._trading_client.get_orders, request)
        orders = [_map_order(o) for o in raw]
        if status is not None:
            orders = [o for o in orders if o.status is status]
        return orders
