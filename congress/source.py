import logging
from datetime import date, datetime

import httpx

from .models import CongressTrade, TransactionType

logger = logging.getLogger(__name__)

# The House Clerk (disclosures-clerk.house.gov) only publishes Periodic
# Transaction Reports as scanned PDFs -- no structured feed. This is a free,
# no-key, community-maintained mirror that parses those PDFs into JSON,
# scraping the same official source. See project memory for the tradeoff
# considered against paid APIs (Quiver Quant etc.) and the Senate side
# (senatestockwatcher's data repo is abandoned as of this writing, so only
# House coverage is wired up for now).
_HOUSE_TRANSACTIONS_URL = "https://raw.githubusercontent.com/TattooedHead/house-stock-watcher-data/main/data/all_transactions.json"

_TYPE_MAP = {"Purchase": TransactionType.BUY, "Sale": TransactionType.SELL}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        return None


def _parse_row(row: dict) -> CongressTrade | None:
    ticker = (row.get("ticker") or "").strip().upper()
    if not ticker or not ticker.isalpha() or len(ticker) > 5:
        return None  # blank/placeholder tickers, funds without a clean ticker, etc.

    transaction_type = _TYPE_MAP.get(row.get("type"))
    if transaction_type is None:
        return None  # "Exchange" and anything unrecognized isn't a clean buy/sell direction

    transaction_date = _parse_date(row.get("transaction_date"))
    disclosure_date = _parse_date(row.get("disclosure_date"))
    filing_id = row.get("filing_id")
    representative = (row.get("representative") or "").strip()
    if transaction_date is None or disclosure_date is None or not filing_id or not representative:
        return None

    return CongressTrade(
        representative=representative,
        chamber="house",
        ticker=ticker,
        transaction_type=transaction_type,
        transaction_date=transaction_date,
        disclosure_date=disclosure_date,
        amount_mid=float(row.get("amount_mid") or 0.0),
        filing_id=str(filing_id),
        source_url=row.get("source_url") or "",
    )


class HouseStockWatcherSource:
    """Fetches every House-of-Representatives stock trade disclosure from
    the free JSON mirror above and parses it into CongressTrade rows,
    dropping anything malformed rather than raising -- one bad row (garbled
    OCR, a missing field) shouldn't take down the whole sync."""

    def __init__(self, http_client: httpx.AsyncClient | None = None, url: str = _HOUSE_TRANSACTIONS_URL):
        self._client = http_client or httpx.AsyncClient(timeout=30.0)
        self._url = url

    async def fetch(self) -> list[CongressTrade]:
        response = await self._client.get(self._url)
        response.raise_for_status()
        rows = response.json()
        trades = []
        skipped = 0
        for row in rows:
            trade = _parse_row(row)
            if trade is None:
                skipped += 1
                continue
            trades.append(trade)
        logger.info("congress trade source: parsed %d trades, skipped %d malformed rows", len(trades), skipped)
        return trades
