import asyncio
from datetime import datetime, timedelta

from broker.base import BrokerAdapter

from .optionable_repository import OptionableSymbolsRepository
from .universe_repository import UniverseRepository


class UniverseManager:
    """Two universes sharing the same top-N-by-volume active-symbols cache:

    - `get_universe` intersects it with every symbol that actually has a
      listed option contract — the options loop can't trade a name Alpaca
      doesn't offer options on, no matter how actively it trades.
    - `get_active_symbols` returns the top-N-by-volume list on its own, with
      no options-listing filter — the stock loop buys shares directly and
      has no need for a listed option chain, so intersecting it away would
      only shrink its tradeable universe for no reason (this cut it from 100
      to 75 symbols before the split; see project memory).

    Both the active-symbols and optionable-symbols caches recalculate on
    their own fixed interval, not a static list. N defaults to 100, the
    ceiling Alpaca's most-actives screener enforces.

    Optional price ceiling: max_price on get_universe/get_active_symbols
    drops symbols whose last quoted price alone would exceed it -- there's
    no point scanning/scoring a name a trade can never actually be sized
    into (e.g. MSFT/SPY/QQQ against a few-hundred-dollar paper bankroll; see
    project memory on the exposure-cap diagnosis this came from). Prices are
    fetched live (no batch-quote endpoint exists) and cached in-memory for
    price_cache_ttl, since polling ~100 quotes every scan cycle just to
    apply a slow-moving ceiling would be its own waste."""

    def __init__(
        self,
        broker: BrokerAdapter,
        repository: UniverseRepository,
        optionable_repository: OptionableSymbolsRepository,
        size: int = 100,
        refresh_interval: timedelta = timedelta(days=7),
        price_cache_ttl: timedelta = timedelta(hours=1),
    ):
        self._broker = broker
        self._repository = repository
        self._optionable_repository = optionable_repository
        self._size = size
        self._refresh_interval = refresh_interval
        self._price_cache_ttl = price_cache_ttl
        self._price_cache: dict[str, float] = {}
        self._price_cache_attempted: set[str] = set()
        self._price_cache_computed_at: datetime | None = None

    async def get_universe(self, now: datetime, max_price: float | None = None) -> list[str]:
        active = await self._get_active_symbols(now)
        optionable = set(await self._get_optionable_symbols(now))
        symbols = [s for s in active if s in optionable]
        return await self._apply_price_ceiling(now, symbols, max_price)

    async def get_active_symbols(self, now: datetime, max_price: float | None = None) -> list[str]:
        symbols = await self._get_active_symbols(now)
        return await self._apply_price_ceiling(now, symbols, max_price)

    async def _apply_price_ceiling(self, now: datetime, symbols: list[str], max_price: float | None) -> list[str]:
        if max_price is None:
            return symbols
        prices = await self._get_prices(now, symbols)
        # A symbol whose price couldn't be determined (quote fetch failed, or
        # a non-positive/missing ask) is excluded rather than let through --
        # the whole point is not wasting a scan cycle on something we can't
        # even confirm is affordable.
        return [s for s in symbols if s in prices and prices[s] <= max_price]

    async def _get_prices(self, now: datetime, symbols: list[str]) -> dict[str, float]:
        stale = (
            self._price_cache_computed_at is None
            or now - self._price_cache_computed_at >= self._price_cache_ttl
            # a previous refresh_active/refresh_optionable call can hand
            # _apply_price_ceiling a symbol list the cache never attempted
            or not set(symbols) <= self._price_cache_attempted
        )
        if not stale:
            return self._price_cache

        async def _price(symbol: str) -> tuple[str, float] | None:
            try:
                quote = await self._broker.get_latest_quote(symbol)
            except Exception:
                return None
            return (symbol, quote.ask_price) if quote.ask_price > 0 else None

        results = await asyncio.gather(*(_price(s) for s in symbols))
        self._price_cache = {symbol: price for r in results if r is not None for symbol, price in [r]}
        self._price_cache_attempted = set(symbols)
        self._price_cache_computed_at = now
        return self._price_cache

    async def refresh(self, now: datetime) -> list[str]:
        """Force-refreshes both the active-symbols and optionable-symbols
        caches regardless of staleness, and returns their intersection."""
        active = await self._refresh_active(now)
        optionable = set(await self._refresh_optionable(now))
        return [s for s in active if s in optionable]

    async def _get_active_symbols(self, now: datetime) -> list[str]:
        latest = await self._repository.latest_snapshot()
        if latest is not None:
            computed_at, symbols = latest
            if now - computed_at < self._refresh_interval:
                return symbols
        return await self._refresh_active(now)

    async def _refresh_active(self, now: datetime) -> list[str]:
        active = await self._broker.get_most_active_symbols(top=self._size)
        await self._repository.save_snapshot(now, active)
        return [a.symbol for a in active]

    async def _get_optionable_symbols(self, now: datetime) -> list[str]:
        latest = await self._optionable_repository.latest_snapshot()
        if latest is not None:
            computed_at, symbols = latest
            if now - computed_at < self._refresh_interval:
                return symbols
        return await self._refresh_optionable(now)

    async def _refresh_optionable(self, now: datetime) -> list[str]:
        symbols = await self._broker.get_optionable_symbols()
        await self._optionable_repository.save_snapshot(now, symbols)
        return symbols
