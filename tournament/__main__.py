"""CLI:

    python -m tournament run [--market equities|forex|both]
                             [--equities-days N] [--forex-days N]
                             [--equities-timeframe 15Min] [--max-symbols N] [--no-save]
    python -m tournament promote --market equities|forex
                                 [--strategy "<name>"] [--yes]

`run` prints one leaderboard per market and (unless --no-save) writes JSON +
Markdown under tournament/results/. `promote` takes the winner from the last
saved run (or an explicit --strategy) and edits decision_engine/scoring.py +
.env; it is a dry run printing a diff unless --yes is given.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from config.settings import Settings
from data.database import Database

from .promote import promote as do_promote
from .promote import winner_from_latest
from .report import persist, render_leaderboard
from .runner import EQUITIES_DAYS_DEFAULT, EQUITIES_TIMEFRAME, FOREX_DAYS_DEFAULT, run_tournament


async def _run(args: argparse.Namespace) -> int:
    db = Database.from_settings(Settings())
    await db.connect()
    try:
        boards = await run_tournament(
            db.pool,
            market=args.market,
            equities_days=args.equities_days,
            forex_days=args.forex_days,
            equities_timeframe=args.equities_timeframe,
            max_symbols=args.max_symbols,
        )
    finally:
        await db.disconnect()

    for board in boards.values():
        print(render_leaderboard(board))
        print()

    if not args.no_save:
        path = persist(boards)
        print(f"saved: {path}  (and {path.parent / 'latest.json'})")
    return 0


def _promote(args: argparse.Namespace) -> int:
    strategy_name = args.strategy or winner_from_latest(args.market)
    if not args.strategy:
        print(f"winner of last saved {args.market} run: {strategy_name!r}\n")
    print(do_promote(args.market, strategy_name, apply=args.yes))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tournament")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the tournament and print leaderboards")
    run_p.add_argument("--market", choices=("equities", "forex", "both"), default="both")
    run_p.add_argument("--equities-days", type=int, default=EQUITIES_DAYS_DEFAULT,
                       help=f"equities lookback window in calendar days (default {EQUITIES_DAYS_DEFAULT})")
    run_p.add_argument("--forex-days", type=int, default=FOREX_DAYS_DEFAULT,
                       help=f"forex lookback window in calendar days (default {FOREX_DAYS_DEFAULT})")
    run_p.add_argument("--equities-timeframe", default=EQUITIES_TIMEFRAME,
                       help=f"bar timeframe for the equities board (default {EQUITIES_TIMEFRAME}; e.g. 5Min, 1Hour)")
    run_p.add_argument("--max-symbols", type=int, default=None,
                       help="cap the universe per market (first N alphabetically) — for a quick smoke run")
    run_p.add_argument("--no-save", action="store_true", help="print only; do not write results files")

    prom_p = sub.add_parser("promote", help="write a winner into live config (scoring.py + .env)")
    prom_p.add_argument("--market", choices=("equities", "forex"), required=True)
    prom_p.add_argument("--strategy", help="strategy name; defaults to the winner of the last saved run")
    prom_p.add_argument("--yes", action="store_true", help="actually write the changes (otherwise dry-run diff)")

    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "promote":
        return _promote(args)
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
