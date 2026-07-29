"""One-off historical-data backfill for the forex backtest engine.

Pulls OANDA candle history into the same `bars` table equities already use
(bars is asset-agnostic: symbol/timeframe/OHLCV, no schema change needed) via
the existing BarIngestionService -- OandaAdapter.get_bars() duck-types
BrokerAdapter's signature (see its docstring for why OandaAdapter isn't
formally a BrokerAdapter) so the same backfill/ingest machinery equities uses
works unchanged for forex history.

Usage: python -m backtesting.backfill_forex_history [--days 180] [--timeframe H1] [--pairs EUR_USD,GBP_USD]
"""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from config.settings import Settings
from data.bars_repository import BarsRepository
from data.database import Database
from data.ingestion import BarIngestionService
from data.schema import apply_schema
from forex.oanda_adapter import OandaAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def backfill_forex_history(
    settings: Settings, days: int, timeframe: str, pairs: list[str] | None = None
) -> dict[str, int]:
    if not settings.oanda_api_key or not settings.oanda_account_id:
        raise RuntimeError("OANDA_API_KEY/OANDA_ACCOUNT_ID must be set to backfill forex history")

    db = Database.from_settings(settings)
    await db.connect()
    adapter = OandaAdapter(settings.oanda_api_key, settings.oanda_account_id, live=settings.trading_mode == "live")
    try:
        await apply_schema(db.pool)
        target_pairs = pairs or await adapter.get_tradeable_pairs()
        logger.info("backfilling %d pairs, %d days of %s candles", len(target_pairs), days, timeframe)

        bars_repository = BarsRepository(db.pool)
        ingestion_service = BarIngestionService(adapter, bars_repository)  # type: ignore[arg-type]
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        return await ingestion_service.backfill(target_pairs, timeframe, start, end)
    finally:
        await adapter.aclose()
        await db.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180, help="How many days of history to backfill (default 180)")
    parser.add_argument("--timeframe", default="H1", help="OANDA granularity code (default H1)")
    parser.add_argument("--pairs", default=None, help="Comma-separated pairs; default is every OANDA-tradeable pair")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    pairs = args.pairs.split(",") if args.pairs else None
    results = await backfill_forex_history(Settings(), args.days, args.timeframe, pairs)
    for pair, count in sorted(results.items()):
        logger.info("%s: %d bars stored", pair, count)
    logger.info("done: %d bars total across %d pairs", sum(results.values()), len(results))


if __name__ == "__main__":
    asyncio.run(_main())
