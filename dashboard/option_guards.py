"""Shared entry/exit helpers for the option loops (momentum, breakout, S/R):
the underlying's recent 5Min bars, and a contract spread check. See
decision_engine/entry_timing.py for the pure logic."""

from datetime import datetime, timedelta

from broker.models import Bar, OptionContract
from decision_engine.entry_timing import drop_forming_bar

from .context import AppContext

_FIVE_MIN_LOOKBACK_DAYS = 5


async def recent_5m_bars(context: AppContext, symbol: str, now: datetime) -> list[Bar]:
    """The underlying's 5Min bars, including the one still forming."""
    await context.ingestion_service.ingest_incremental(symbol, "5Min", end=now)
    return await context.bars_repository.get_bars(symbol, "5Min", now - timedelta(days=_FIVE_MIN_LOOKBACK_DAYS), now)


async def latest_completed_5m_close(context: AppContext, symbol: str, now: datetime) -> float | None:
    completed = drop_forming_bar(await recent_5m_bars(context, symbol, now), "5Min", now)
    return completed[-1].close if completed else None


def spread_rejection(contract: OptionContract, max_spread_pct: float) -> str | None:
    """Why contract's quote is too wide to trade, or None if it's fine."""
    if contract.bid is None or contract.ask is None or contract.bid <= 0 or contract.ask < contract.bid:
        return f"no two-sided quote (bid={contract.bid}, ask={contract.ask})"
    mid = (contract.bid + contract.ask) / 2
    spread = (contract.ask - contract.bid) / mid
    if spread > max_spread_pct:
        return f"bid-ask spread {spread:.0%} of mid ({contract.bid:.2f}/{contract.ask:.2f}) > max {max_spread_pct:.0%}"
    return None
