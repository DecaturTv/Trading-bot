from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from broker.models import OrderSide

from .models import OpenForexPosition


@dataclass(frozen=True)
class ExposureCheckResult:
    passed: bool
    reason: str | None = None


def _currencies(pair: str) -> tuple[str, str]:
    base, quote = pair.split("_")
    return base, quote


def _long_short(pair: str, side: OrderSide) -> tuple[str, str]:
    """Which currency a position is long vs short -- buying a pair means
    long the base/short the quote, selling is the reverse."""
    base, quote = _currencies(pair)
    return (base, quote) if side is OrderSide.BUY else (quote, base)


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


def check_currency_direction_conflict(
    pair: str, side: OrderSide, open_positions: Sequence[OpenForexPosition]
) -> ExposureCheckResult:
    """Rejects a candidate that would net against an already-open position on
    a shared currency -- e.g. buying AUD_NZD (long AUD) while AUD_JPY is open
    sell (short AUD) is two spreads paid to hold a position that mostly
    cancels itself out, not two independent bets (this happened live: see
    project memory on the forex strategy-contradiction diagnosis).

    Unlike check_currency_concentration's same-direction stacking cap, this
    applies regardless of count -- even a single existing opposite-direction
    position on a shared currency is a contradiction, not diversification.
    Needs the candidate's side, so it can only run once the signal's
    direction is known (after check_currency_concentration's cheap early
    pre-filter, not before).
    """
    candidate_long, candidate_short = _long_short(pair, side)

    for position in open_positions:
        pos_long, pos_short = _long_short(position.pair, position.side)
        if candidate_long == pos_short or candidate_short == pos_long:
            conflicting_currency = candidate_long if candidate_long == pos_short else candidate_short
            return ExposureCheckResult(
                passed=False,
                reason=(
                    f"{pair} ({side.value}) would net against already-open {position.pair} "
                    f"({position.side.value}) -- both take opposite positions on {conflicting_currency}"
                ),
            )
    return ExposureCheckResult(passed=True)
