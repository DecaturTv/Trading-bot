from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    PENDING_CANCEL = "pending_cancel"
    EXPIRED = "expired"
    OTHER = "other"


@dataclass(frozen=True)
class Account:
    account_id: str
    equity: float
    cash: float
    buying_power: float
    currency: str


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    side: OrderSide
    avg_entry_price: float
    market_value: float
    unrealized_pl: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    timestamp: datetime


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    qty: float
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: float | None = None
    stop_price: float | None = None


@dataclass(frozen=True)
class ActiveSymbol:
    symbol: str
    volume: float


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class OptionGreeks:
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    rho: float | None


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying_symbol: str
    strike: float
    expiration: date
    right: OptionRight
    bid: float | None
    ask: float | None
    last_price: float | None
    implied_volatility: float | None
    greeks: OptionGreeks | None


@dataclass(frozen=True)
class Order:
    order_id: str
    symbol: str
    qty: float
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    filled_qty: float
    filled_avg_price: float | None
    submitted_at: datetime | None
    filled_at: datetime | None
    legs: "list[Order] | None" = None  # populated for multi-leg combo orders


class PositionIntent(str, Enum):
    BUY_TO_OPEN = "buy_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_OPEN = "sell_to_open"
    SELL_TO_CLOSE = "sell_to_close"


@dataclass(frozen=True)
class MultiLegOrderLeg:
    symbol: str
    side: OrderSide
    position_intent: PositionIntent
    ratio_qty: int = 1


@dataclass(frozen=True)
class MultiLegOrderRequest:
    legs: list[MultiLegOrderLeg]
    qty: float
    limit_price: float  # net price per spread unit (positive = net debit)
    time_in_force: TimeInForce = TimeInForce.DAY
