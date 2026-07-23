from datetime import datetime, timedelta

from broker.base import BrokerAdapter

from .optionable_repository import OptionableSymbolsRepository
from .universe_repository import UniverseRepository


class UniverseManager:
    """Universe = top-N-by-volume intersected with every symbol that actually
    has a listed option contract (an options-only bot can't trade a name
    Alpaca doesn't offer options on, no matter how actively it trades) — both
    sides recalculated on their own fixed interval, not a static list. N
    defaults to 100, the ceiling Alpaca's most-actives screener enforces."""

    def __init__(
        self,
        broker: BrokerAdapter,
        repository: UniverseRepository,
        optionable_repository: OptionableSymbolsRepository,
        size: int = 100,
        refresh_interval: timedelta = timedelta(days=7),
    ):
        self._broker = broker
        self._repository = repository
        self._optionable_repository = optionable_repository
        self._size = size
        self._refresh_interval = refresh_interval

    async def get_universe(self, now: datetime) -> list[str]:
        active = await self._get_active_symbols(now)
        optionable = set(await self._get_optionable_symbols(now))
        return [s for s in active if s in optionable]

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
