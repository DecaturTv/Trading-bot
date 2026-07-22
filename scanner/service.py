import logging
from datetime import datetime, timedelta

from broker.base import BrokerAdapter

from .models import ScanHit
from .scans import scan_gap, scan_momentum, scan_unusual_volume
from .universe import UniverseManager

logger = logging.getLogger(__name__)

_SCAN_FUNCTIONS = (scan_unusual_volume, scan_gap, scan_momentum)


class ScannerService:
    def __init__(self, broker: BrokerAdapter, universe: UniverseManager, bars_lookback: int = 30):
        self._broker = broker
        self._universe = universe
        self._bars_lookback = bars_lookback

    async def run_scans(self, now: datetime) -> list[ScanHit]:
        symbols = await self._universe.get_universe(now)
        hits: list[ScanHit] = []
        for symbol in symbols:
            bars = await self._broker.get_bars(
                symbol, "1Day", now - timedelta(days=self._bars_lookback), now
            )
            if not bars:
                logger.warning("no bars returned for %s, skipping scans", symbol)
                continue
            for scan_fn in _SCAN_FUNCTIONS:
                hit = scan_fn(symbol, bars)
                if hit is not None:
                    hits.append(hit)
        return hits
