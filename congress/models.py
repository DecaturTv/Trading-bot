from dataclasses import dataclass
from datetime import date
from enum import Enum


class TransactionType(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class CongressTrade:
    """One line item from a member of Congress's Periodic Transaction Report
    (PTR), filed under the STOCK Act. Disclosure is public record — trading
    on it is legal (unlike the material-nonpublic-information trading the
    STOCK Act itself exists to police); see congress/source.py for where
    this data comes from."""

    representative: str
    chamber: str
    ticker: str
    transaction_type: TransactionType
    transaction_date: date
    disclosure_date: date
    amount_mid: float
    filing_id: str
    source_url: str
