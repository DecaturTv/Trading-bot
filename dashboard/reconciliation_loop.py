"""Broker-vs-tracking reconciliation: the equities sleeves against the shared
Alpaca account, and the forex books against OANDA.

Every equities sleeve (momentum options, breakout options, S/R options,
direct stock, S/R stock) tracks its own positions in its own table, and all
of them trade through one Alpaca account. Nothing ever compared the union of
those tables against what Alpaca actually holds, so both failure directions
went unnoticed for weeks:

- tracked but not held (phantoms): ETHA/NVDL/ONDS/SOXS entries expired
  unfilled on 2026-09-01/02 and were tracked until 09-28;
- held but not tracked (strays): the INTC breakout calls untracked on
  2026-08-31 and exercised into 800 shares on 09-18, plus a string of
  options that rode untracked into expiry on 09-18/09-25.

The fill checks on entry and close (execution/entry_confirmation.py,
trade_management/close_confirmation.py) close the known causes; this cycle
is the backstop for whatever cause comes next. Confirmed phantoms and
strays are repaired automatically (see reconciliation_fixes.py) unless
RECONCILIATION_AUTO_FIX=false; anything it can't safely repair on its own
(quantity mismatches, or more drift in one book than
_MAX_AUTO_FIXES_PER_BOOK, which smells like a bad read rather than real
drift) is alerted instead.

Forex is reconciled by OANDA trade ID rather than by pair -- both forex
books (per-pair technical loop, cross-sectional momentum) record the exact
trade they opened, so the ID is the precise key, and the xsmom rebalance
deliberately drops tracking when a close fails (a stray by construction).

A discrepancy must show up on two consecutive runs before it alerts: a fill
lands at the broker a moment before the loop persists it (and a close
leaves the broker a moment before the loop untracks it), so a single
sighting can be a race rather than drift.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from alerts.models import Alert, Severity

from .context import AppContext
from .reconciliation_fixes import auto_fix

logger = logging.getLogger(__name__)

# More confirmed discrepancies than this in one book at once looks systemic
# (a repository or broker read going wrong) -- acting on each of them could
# close real positions or drop real tracking in bulk, so alert instead.
_MAX_AUTO_FIXES_PER_BOOK = 3


@dataclass(frozen=True)
class Discrepancy:
    symbol: str
    tracked_qty: float
    broker_qty: float

    @property
    def kind(self) -> str:
        if self.broker_qty == 0:
            return "tracked_not_held"
        if self.tracked_qty == 0:
            return "held_not_tracked"
        return "qty_mismatch"

    def describe(self) -> str:
        return f"{self.symbol}: tracked={self.tracked_qty:g} broker={self.broker_qty:g} ({self.kind})"


@dataclass
class ReconciliationState:
    """Discrepancies seen on the previous run, for the two-sighting rule."""

    previous: set[Discrepancy] = field(default_factory=set)


@dataclass(frozen=True)
class _Book:
    name: str  # shown in alerts: "equities" / "forex"
    tracked: dict[str, float]
    held: dict[str, float]
    ignore: frozenset[str]


def diff_positions(tracked: dict[str, float], held: dict[str, float], ignore: frozenset[str] = frozenset()) -> set[Discrepancy]:
    """Compares absolute quantities per broker symbol (OCC symbol for options).
    Absolute because tracking stores unsigned qty and a sell-side leg shows up
    at the broker as a negative position; direction mismatches aren't a
    failure mode seen so far, missing/extra positions are."""
    discrepancies = set()
    for symbol in tracked.keys() | held.keys():
        if symbol in ignore:
            continue
        tracked_qty = abs(tracked.get(symbol, 0.0))
        broker_qty = abs(held.get(symbol, 0.0))
        if tracked_qty != broker_qty:
            discrepancies.add(Discrepancy(symbol=symbol, tracked_qty=tracked_qty, broker_qty=broker_qty))
    return discrepancies


async def tracked_quantities(context: AppContext) -> dict[str, float]:
    """Union of every equities sleeve's tracked positions, keyed by the symbol
    the broker reports them under. Summed per symbol, since two sleeves can
    legitimately hold the same stock."""
    tracked: dict[str, float] = defaultdict(float)
    for repo in (context.position_repository, context.breakout_position_repository, context.sr_option_position_repository):
        for record in await repo.get_all():
            for leg in record.legs:
                tracked[leg.symbol] += record.state.qty
    for repo in (context.stock_position_repository, context.sr_stock_position_repository):
        for record in await repo.get_all():
            tracked[record.symbol] += record.state.qty
    return dict(tracked)


async def tracked_forex_units(context: AppContext) -> dict[str, float]:
    """Both forex books' tracked trades, keyed "PAIR:trade_id" so alerts name
    the pair and the ignore list can match either the pair or one trade."""
    tracked: dict[str, float] = {}
    for pos in await context.forex_position_repository.get_all():
        tracked[f"{pos.pair}:{pos.oanda_trade_id}"] = pos.units
    if context.forex_xsmom_repository is not None:
        for pos in await context.forex_xsmom_repository.get_all():
            tracked[f"{pos.pair}:{pos.oanda_trade_id}"] = pos.units
    return tracked


async def _equities_book(context: AppContext, ignore: frozenset[str]) -> _Book:
    tracked = await tracked_quantities(context)
    held = {p.symbol: p.qty for p in await context.broker.get_positions()}
    return _Book("equities", tracked, held, ignore)


async def _forex_book(context: AppContext, ignore: frozenset[str]) -> _Book:
    tracked = await tracked_forex_units(context)
    held = {f"{pair}:{trade_id}": units for trade_id, (pair, units) in (await context.forex_broker.get_open_trades()).items()}
    # Let a whole pair ("EUR_USD") be ignored as well as a single "EUR_USD:1234" trade.
    keys = tracked.keys() | held.keys()
    ignore = ignore | {k for k in keys if k.split(":", 1)[0] in ignore}
    return _Book("forex", tracked, held, ignore)


async def reconciliation_cycle(context: AppContext, now: datetime, state: ReconciliationState) -> None:
    ignore = frozenset(context.settings.reconciliation_ignore_symbols)
    builders = [_equities_book]
    if context.forex_broker is not None and context.forex_position_repository is not None:
        builders.append(_forex_book)

    current: dict[Discrepancy, str] = {}
    for build in builders:
        # One broker being unreachable shouldn't blind the check on the other.
        try:
            book = await build(context, ignore)
        except Exception:
            logger.exception("reconciliation: could not read the %s book this run", build.__name__.strip("_").removesuffix("_book"))
            continue
        found = diff_positions(book.tracked, book.held, book.ignore)
        logger.info(
            "reconciliation (%s): %d tracked, %d held at broker, %d discrepancies",
            book.name, len(book.tracked), len(book.held), len(found),
        )
        current.update({d: book.name for d in found})

    confirmed = current.keys() & state.previous
    state.previous = set(current)

    per_book: dict[str, int] = defaultdict(int)
    for d in confirmed:
        per_book[current[d]] += 1

    for d in sorted(confirmed, key=lambda d: d.symbol):
        book_name = current[d]
        logger.warning("reconciliation discrepancy (%s): %s", book_name, d.describe())

        action = None
        if context.settings.reconciliation_auto_fix and per_book[book_name] <= _MAX_AUTO_FIXES_PER_BOOK:
            try:
                action = await auto_fix(context, book_name, d.kind, d.symbol, now)
            except Exception:
                logger.exception("reconciliation auto-fix failed for %s; will retry next run", d.symbol)
        if action is not None:
            detail = f"Auto-fixed: {action}."
        else:
            detail = (
                "Not fixed automatically -- check the broker and the tracking table. Add the symbol to "
                "RECONCILIATION_IGNORE_SYMBOLS if it's a known, deliberately untracked position."
            )

        await context.alert_manager.send(
            Alert(
                title=f"Position mismatch ({book_name}): {d.symbol}",
                message=f"{d.describe()}. {detail}",
                severity=Severity.WARNING,
                timestamp=now,
                # Keyed on the exact mismatch so a change (e.g. a partial
                # close) re-alerts right away, while an unchanged one only
                # repeats every AlertManager resend interval.
                dedup_key=f"reconciliation-{d.symbol}-{d.tracked_qty:g}-{d.broker_qty:g}",
            )
        )
