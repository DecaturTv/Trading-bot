from abc import ABC, abstractmethod
from datetime import date, datetime

from .models import (
    Account,
    ActiveSymbol,
    Bar,
    MultiLegOrderRequest,
    Order,
    OrderRequest,
    OrderStatus,
    OptionContract,
    Position,
    Quote,
)


class BrokerAdapter(ABC):
    """Interface every equities/options broker integration must implement.

    scanner/, decision_engine/, risk/, and execution/ depend only on this
    interface, never on a broker SDK directly — that's what makes swapping
    Alpaca -> Schwab safe later without touching the rest of the system.
    Forex uses a separate adapter (forex/) since margin/leverage mechanics
    don't fit this interface.
    """

    @abstractmethod
    async def get_account(self) -> Account: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    async def get_latest_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    async def get_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Bar]: ...

    @abstractmethod
    async def get_most_active_symbols(self, top: int = 20) -> list[ActiveSymbol]: ...

    @abstractmethod
    async def get_option_chain(
        self,
        underlying_symbol: str,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
    ) -> list[OptionContract]: ...

    @abstractmethod
    async def submit_order(self, order: OrderRequest) -> Order: ...

    @abstractmethod
    async def submit_multi_leg_order(self, order: MultiLegOrderRequest) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Order: ...

    @abstractmethod
    async def list_orders(self, status: OrderStatus | None = None) -> list[Order]: ...
