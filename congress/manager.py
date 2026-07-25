from datetime import datetime, timedelta

from .models import CongressTrade
from .repository import CongressTradeRepository
from .source import HouseStockWatcherSource


class CongressTradeManager:
    """Lazily refreshes the local congress_trades table from the free source
    on access, same staleness-check pattern as UniverseManager -- no
    dedicated scheduler job needed. The source has no incremental fetch (it's
    one JSON blob of everything filed to date), so a refresh always re-pulls
    the whole thing; upsert_many's ON CONFLICT DO NOTHING makes that cheap
    and idempotent."""

    def __init__(
        self,
        source: HouseStockWatcherSource,
        repository: CongressTradeRepository,
        refresh_interval: timedelta = timedelta(hours=6),
    ):
        self._source = source
        self._repository = repository
        self._refresh_interval = refresh_interval

    async def get_recent_trades(self, ticker: str, now: datetime, lookback_days: int = 30) -> list[CongressTrade]:
        await self._refresh_if_stale(now)
        since = (now - timedelta(days=lookback_days)).date()
        return await self._repository.get_recent_for_ticker(ticker, since)

    async def _refresh_if_stale(self, now: datetime) -> None:
        last_synced = await self._repository.latest_synced_at()
        if last_synced is not None and now - last_synced < self._refresh_interval:
            return
        trades = await self._source.fetch()
        await self._repository.upsert_many(trades)
        await self._repository.record_synced_at(now)
