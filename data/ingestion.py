import logging
from datetime import datetime, timedelta

from broker.base import BrokerAdapter

from .bars_repository import BarsRepository

logger = logging.getLogger(__name__)


class BarIngestionService:
    """Fetches historical bars from a BrokerAdapter and persists them."""

    def __init__(self, broker: BrokerAdapter, repository: BarsRepository):
        self._broker = broker
        self._repository = repository

    async def ingest(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> int:
        bars = await self._broker.get_bars(symbol, timeframe, start, end)
        stored = await self._repository.upsert_bars(timeframe, bars)
        logger.info("ingested %d %s bars for %s", stored, timeframe, symbol)
        return stored

    async def ingest_incremental(
        self,
        symbol: str,
        timeframe: str,
        end: datetime,
        default_lookback: timedelta = timedelta(days=30),
    ) -> int:
        """Ingest bars since the last one stored, or default_lookback if none exist yet."""
        latest = await self._repository.latest_timestamp(symbol, timeframe)
        start = latest + timedelta(seconds=1) if latest else end - default_lookback
        if start >= end:
            return 0
        return await self.ingest(symbol, timeframe, start, end)

    async def backfill(self, symbols: list[str], timeframe: str, start: datetime, end: datetime) -> dict[str, int]:
        # Sequential by design: avoids tripping the broker's rate limits until
        # real limits are known; revisit with a bounded concurrency pool if needed.
        results = {}
        for symbol in symbols:
            results[symbol] = await self.ingest(symbol, timeframe, start, end)
        return results
