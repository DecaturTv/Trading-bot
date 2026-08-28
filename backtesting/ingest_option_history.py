"""Download real historical option OHLCV into `option_bars` so the backtest
engine can mark positions against actual traded prices instead of the
Black-Scholes stub in simulated_pricing.py.

For each underlying it:
  1. reads the underlying's daily price range over [start, end] (from the
     `bars` table the tournament already uses, falling back to the API),
  2. asks Alpaca for every contract (active + expired) whose expiration lands
     in the DTE band a backtest could target,
  3. keeps the strikes within a band around the observed price range,
  4. downloads their bars in batches and upserts them.

Idempotent-ish: a contract whose stored bars already reach `end` (or its own
expiration) is skipped on re-run.

Usage:
  python -m backtesting.ingest_option_history --underlyings AAPL,NVDA,SPY \\
      --start 2026-06-23 --end 2026-08-28 --timeframe 15Min
  python -m backtesting.ingest_option_history --underlyings tournament \\
      --start 2026-06-23 --end 2026-08-28
"""

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, time, timedelta, timezone

from broker.alpaca_adapter import AlpacaAdapter
from config.settings import Settings
from data.bars_repository import BarsRepository
from data.database import Database
from data.option_bars_repository import OptionBarsRepository
from data.option_history_schema import apply_option_history_schema
from data.schema import apply_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_TIMEFRAME_TO_STOCK_DAILY = "1Day"
_OCC_RE = re.compile(r"[A-Z]{1,5}\d{6}[CP]\d{8}")


def _dte_trading_to_calendar(days: int) -> int:
    return round(days * 7 / 5)


async def _tournament_universe(pool) -> list[str]:
    rows = await pool.fetch(
        "SELECT DISTINCT symbol FROM bars WHERE symbol !~ '^[A-Z]{3}_[A-Z]{3}$' ORDER BY symbol"
    )
    return [r["symbol"] for r in rows]


async def _price_band(
    bars_repo: BarsRepository, adapter: AlpacaAdapter, underlying: str, start: datetime, end: datetime, band_pct: float
) -> tuple[float, float] | None:
    bars = await bars_repo.get_bars(underlying, _TIMEFRAME_TO_STOCK_DAILY, start, end)
    if not bars:
        bars = await adapter.get_bars(underlying, _TIMEFRAME_TO_STOCK_DAILY, start, end)
    if not bars:
        return None
    lo = min(b.low for b in bars) * (1 - band_pct)
    hi = max(b.high for b in bars) * (1 + band_pct)
    return max(lo, 0.01), hi


async def ingest_underlying(
    adapter: AlpacaAdapter,
    option_repo: OptionBarsRepository,
    bars_repo: BarsRepository,
    underlying: str,
    start: datetime,
    end: datetime,
    timeframe: str,
    dte_min: int,
    dte_max: int,
    strike_band_pct: float,
    batch_size: int,
    force: bool = False,
) -> dict[str, int]:
    if not force and await option_repo.bar_count_for_underlying(underlying, timeframe) > 0:
        logger.info("%s: already has option bars, skipping (pass --force to re-fetch)", underlying)
        return {"contracts": 0, "bars": 0, "contracts_with_bars": 0}
    band = await _price_band(bars_repo, adapter, underlying, start, end, strike_band_pct)
    if band is None:
        logger.warning("%s: no underlying price history, skipping", underlying)
        return {"contracts": 0, "bars": 0, "contracts_with_bars": 0}
    price_lo, price_hi = band

    exp_lo = (start + timedelta(days=_dte_trading_to_calendar(dte_min))).date()
    exp_hi = (end + timedelta(days=_dte_trading_to_calendar(dte_max))).date()
    contracts = await adapter.get_historical_option_contracts(underlying, exp_lo, exp_hi)
    # Alpaca's contract list can include corporate-action-adjusted symbols
    # (e.g. "1SOFI260918C00022500") that its own bars endpoint then 400s on,
    # failing the whole batch — keep only standard OCC symbols.
    in_band = [
        c for c in contracts
        if price_lo <= c.strike <= price_hi and _OCC_RE.fullmatch(c.symbol)
    ]
    if not in_band:
        logger.warning("%s: 0/%d contracts in strike band [%.2f, %.2f]", underlying, len(contracts), price_lo, price_hi)
        return {"contracts": 0, "bars": 0, "contracts_with_bars": 0}
    await option_repo.upsert_contracts(in_band)

    # Skip contracts whose stored bars already reach `end` or the contract's
    # own expiration (expired contracts get no further bars).
    to_fetch: list[str] = []
    for c in in_band:
        latest = await option_repo.latest_timestamp(c.symbol, timeframe)
        expiry_close = datetime.combine(c.expiration, time(20, 0), tzinfo=timezone.utc)
        if latest is not None and (latest >= end or latest >= expiry_close):
            continue
        to_fetch.append(c.symbol)

    # Fetch + upsert one batch at a time and drop it — a liquid underlying's
    # full bar set (SPY: ~19k contracts) does not fit in this box's ~1 GB RAM.
    total_bars = 0
    for i in range(0, len(to_fetch), batch_size):
        chunk = to_fetch[i : i + batch_size]
        bars = await adapter.get_option_bars(chunk, timeframe, start, end, batch_size=batch_size)
        total_bars += await option_repo.upsert_option_bars(timeframe, bars)
        del bars

    with_bars = await option_repo.get_contracts_with_bars(
        underlying, timeframe, exp_lo, exp_hi, start, end
    )
    stats = {"contracts": len(in_band), "bars": total_bars, "contracts_with_bars": len(with_bars)}
    logger.info(
        "%s: %d contracts in band, fetched %d (skipped %d cached), +%d bars, %d contracts now have bars",
        underlying, len(in_band), len(to_fetch), len(in_band) - len(to_fetch), total_bars, len(with_bars),
    )
    return stats


async def ingest_option_history(
    settings: Settings,
    underlyings: list[str],
    start: datetime,
    end: datetime,
    timeframe: str,
    dte_min: int,
    dte_max: int,
    strike_band_pct: float,
    batch_size: int,
    force: bool = False,
) -> dict[str, dict[str, int]]:
    db = Database.from_settings(settings)
    await db.connect()
    adapter = AlpacaAdapter.from_settings(settings)
    try:
        await apply_schema(db.pool)
        await apply_option_history_schema(db.pool)
        option_repo = OptionBarsRepository(db.pool)
        bars_repo = BarsRepository(db.pool)

        if underlyings == ["tournament"]:
            underlyings = await _tournament_universe(db.pool)
        logger.info("ingesting option history for %d underlyings, %s bars, %s..%s",
                    len(underlyings), timeframe, start.date(), end.date())

        results: dict[str, dict[str, int]] = {}
        for i, underlying in enumerate(underlyings, 1):
            print(f"[{i}/{len(underlyings)}] {underlying}", file=sys.stderr, flush=True)
            try:
                results[underlying] = await ingest_underlying(
                    adapter, option_repo, bars_repo, underlying, start, end,
                    timeframe, dte_min, dte_max, strike_band_pct, batch_size, force=force,
                )
            except Exception:
                logger.exception("%s: ingestion failed, continuing", underlying)
                results[underlying] = {"contracts": 0, "bars": 0, "contracts_with_bars": 0}
        return results
    finally:
        await db.disconnect()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--underlyings", required=True,
                   help="comma-separated tickers, or 'tournament' for the equities universe in `bars`")
    p.add_argument("--start", required=True, help="backtest window start, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="backtest window end, YYYY-MM-DD")
    p.add_argument("--timeframe", default="15Min", help="option bar timeframe (default 15Min)")
    p.add_argument("--dte-min", type=int, default=15, help="min DTE (trading days) a backtest could target (default 15)")
    p.add_argument("--dte-max", type=int, default=45, help="max DTE (trading days) a backtest could target (default 45)")
    p.add_argument("--strike-band-pct", type=float, default=0.35,
                   help="keep strikes within +/- this fraction of the observed price range (default 0.35)")
    p.add_argument("--batch-size", type=int, default=100, help="option symbols per bar request (default 100)")
    p.add_argument("--force", action="store_true", help="re-fetch underlyings that already have option bars")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    if args.underlyings.strip().lower() == "tournament":
        underlyings = ["tournament"]
    else:
        underlyings = [s.strip().upper() for s in args.underlyings.split(",") if s.strip()]
    results = await ingest_option_history(
        Settings(), underlyings, start, end, args.timeframe,
        args.dte_min, args.dte_max, args.strike_band_pct, args.batch_size, force=args.force,
    )
    total_bars = sum(r["bars"] for r in results.values())
    total_contracts = sum(r["contracts"] for r in results.values())
    total_with_bars = sum(r["contracts_with_bars"] for r in results.values())
    cov = (total_with_bars / total_contracts * 100) if total_contracts else 0.0
    logger.info(
        "done: %d underlyings, %d contracts in band, %d with >=1 bar (%.0f%%), %d bars stored",
        len(results), total_contracts, total_with_bars, cov, total_bars,
    )


if __name__ == "__main__":
    asyncio.run(_main())
