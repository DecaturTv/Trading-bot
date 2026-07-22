from datetime import datetime, timedelta

from broker.base import BrokerAdapter

from .universe_repository import UniverseRepository


class UniverseManager:
    """Top-N-by-volume universe, recalculated on a fixed interval (not a static list)."""

    def __init__(
        self,
        broker: BrokerAdapter,
        repository: UniverseRepository,
        size: int = 20,
        refresh_interval: timedelta = timedelta(days=7),
    ):
        self._broker = broker
        self._repository = repository
        self._size = size
        self._refresh_interval = refresh_interval

    async def get_universe(self, now: datetime) -> list[str]:
        latest = await self._repository.latest_snapshot()
        if latest is not None:
            computed_at, symbols = latest
            if now - computed_at < self._refresh_interval:
                return symbols
        return await self.refresh(now)

    async def refresh(self, now: datetime) -> list[str]:
        active = await self._broker.get_most_active_symbols(top=self._size)
        await self._repository.save_snapshot(now, active)
        return [a.symbol for a in active]
