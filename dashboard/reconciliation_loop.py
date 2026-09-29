"""Broker-vs-tracking reconciliation for the shared Alpaca account.

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
is the backstop for whatever cause comes next. It only alerts -- it never
edits tracking or trades, since the right fix depends on why it drifted.

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

logger = logging.getLogger(__name__)


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


async def reconciliation_cycle(context: AppContext, now: datetime, state: ReconciliationState) -> None:
    tracked = await tracked_quantities(context)
    held = {p.symbol: p.qty for p in await context.broker.get_positions()}
    ignore = frozenset(context.settings.reconciliation_ignore_symbols)

    current = diff_positions(tracked, held, ignore)
    confirmed = current & state.previous
    state.previous = current

    logger.info(
        "reconciliation: %d tracked, %d held at broker, %d discrepancies (%d confirmed)",
        len(tracked), len(held), len(current), len(confirmed),
    )
    for d in sorted(confirmed, key=lambda d: d.symbol):
        logger.warning("reconciliation discrepancy: %s", d.describe())
        await context.alert_manager.send(
            Alert(
                title=f"Position mismatch: {d.symbol}",
                message=(
                    f"{d.describe()}. Nothing was changed automatically -- check the broker and the tracking table. "
                    "Add the symbol to RECONCILIATION_IGNORE_SYMBOLS if it's a known, deliberately untracked position."
                ),
                severity=Severity.WARNING,
                timestamp=now,
                # Keyed on the exact mismatch so a change (e.g. a partial
                # close) re-alerts right away, while an unchanged one only
                # repeats every AlertManager resend interval.
                dedup_key=f"reconciliation-{d.symbol}-{d.tracked_qty:g}-{d.broker_qty:g}",
            )
        )
