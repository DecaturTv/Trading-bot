from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .models import OpenForexPosition


@dataclass(frozen=True)
class ExposureCheckResult:
    passed: bool
    reason: str | None = None


def _currencies(pair: str) -> tuple[str, str]:
    base, quote = pair.split("_")
    return base, quote


def check_currency_concentration(
    pair: str, open_positions: Sequence[OpenForexPosition], max_positions_per_currency: int
) -> ExposureCheckResult:
    """Rejects a candidate pair if either of its currencies already appears
    in max_positions_per_currency or more open positions.

    forex_entry_cycle scans every OANDA-tradeable pair independently, with
    no equivalent of the equities pre_trade_checker's exposure/correlation
    caps -- so nothing stops it stacking several pairs that all key off the
    same currency (e.g. EUR_ZAR + CHF_ZAR + GBP_ZAR is really one bet on
    ZAR, not three independent ones). That's what turned one bad macro move
    into a -6% day that tripped the daily halt in a single session -- see
    project memory.
    """
    base, quote = _currencies(pair)
    candidate_currencies = (base, quote)

    counts: Counter[str] = Counter()
    for position in open_positions:
        pos_base, pos_quote = _currencies(position.pair)
        counts[pos_base] += 1
        counts[pos_quote] += 1

    for currency in candidate_currencies:
        if counts[currency] >= max_positions_per_currency:
            return ExposureCheckResult(
                passed=False,
                reason=(
                    f"{currency} already appears in {counts[currency]} open position(s), "
                    f"at/above cap {max_positions_per_currency}"
                ),
            )
    return ExposureCheckResult(passed=True)
