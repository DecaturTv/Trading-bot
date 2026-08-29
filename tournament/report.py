"""Rendering + persistence for tournament results: a terminal leaderboard, plus
a machine-readable JSON and a Markdown copy written under tournament/results/.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .runner import Leaderboard

RESULTS_DIR = Path(__file__).parent / "results"

_COLS = ("Rank", "Strategy", "P&L $", "Return", "Trades", "Win %", "Avg win", "Avg loss", "Max DD", "Symbols")


def _rows(board: Leaderboard) -> list[tuple[str, ...]]:
    rows = []
    for i, r in enumerate(board.results, start=1):
        marker = " *" if i == 1 else ""
        rows.append(
            (
                f"{i}{marker}",
                r.name,
                f"{r.total_pnl:,.2f}",
                f"{r.return_pct:+.1%}",
                str(r.trade_count),
                f"{r.win_rate:.0%}",
                f"{r.avg_win:,.2f}",
                f"{r.avg_loss:,.2f}",
                f"{r.max_drawdown_pct:.1%}",
                str(r.symbols_traded),
            )
        )
    return rows


def render_leaderboard(board: Leaderboard) -> str:
    rows = _rows(board)
    widths = [max(len(_COLS[c]), *(len(row[c]) for row in rows)) if rows else len(_COLS[c]) for c in range(len(_COLS))]

    def fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.rjust(widths[i]) if i else cell.ljust(widths[i]) for i, cell in enumerate(cells))

    start = board.period_start.date()
    end = board.period_end.date()
    header = (
        f"{board.market.upper()} TOURNAMENT — {board.timeframe} bars, {start} to {end}\n"
        f"starting bankroll ${board.starting_bankroll:,.0f} per strategy · "
        f"{board.symbol_count} symbols with usable history · ranked by total dollar P&L"
    )
    lines = [header, "", fmt(_COLS), "  ".join("-" * w for w in widths)]
    lines += [fmt(row) for row in rows]
    if board.winner:
        lines += ["", f"WINNER: {board.winner.name}  (+${board.winner.total_pnl:,.2f}, {board.winner.return_pct:+.1%})"]

    if any(r.positions_taken or r.positions_skipped_capital or r.positions_skipped_slots for r in board.results):
        lines += ["", "Shared-capital portfolio (one bankroll; positions compete for capital, sizing compounds):"]
        for r in board.results:
            lines.append(
                f"  {r.name:<16} {r.positions_taken} taken, "
                f"skipped {r.positions_skipped_capital} (capital) / {r.positions_skipped_slots} (slots), "
                f"peak {r.peak_concurrent} concurrent"
            )

    if any(r.priced_historical or r.priced_ffill or r.entries_skipped_no_quote for r in board.results):
        lines += ["", "Real-quote coverage (exits priced from real option bars vs forward-filled; entries skipped for no quote):"]
        for r in board.results:
            priced = r.priced_historical + r.priced_ffill
            lines.append(
                f"  {r.name:<16} {priced} priced ({r.real_quote_pct:.0%} real, {r.priced_ffill} ffill), "
                f"{r.entries_skipped_no_quote} skipped, {r.symbols_without_option_data} symbols had no option data"
            )
    return "\n".join(lines)


def _board_dict(board: Leaderboard) -> dict:
    return {
        "market": board.market,
        "timeframe": board.timeframe,
        "period_start": board.period_start.isoformat(),
        "period_end": board.period_end.isoformat(),
        "starting_bankroll": board.starting_bankroll,
        "symbol_count": board.symbol_count,
        "winner": board.winner.name if board.winner else None,
        "results": [
            {
                "rank": i,
                "name": r.name,
                "total_pnl": round(r.total_pnl, 2),
                "return_pct": round(r.return_pct, 4),
                "trade_count": r.trade_count,
                "wins": r.wins,
                "losses": r.losses,
                "win_rate": round(r.win_rate, 4),
                "avg_win": round(r.avg_win, 2),
                "avg_loss": round(r.avg_loss, 2),
                "max_drawdown_pct": round(r.max_drawdown_pct, 4),
                "symbols_traded": r.symbols_traded,
                "ending_bankroll": round(r.ending_bankroll, 2),
                "priced_historical": r.priced_historical,
                "priced_ffill": r.priced_ffill,
                "entries_skipped_no_quote": r.entries_skipped_no_quote,
                "symbols_without_option_data": r.symbols_without_option_data,
                "positions_taken": r.positions_taken,
                "positions_skipped_capital": r.positions_skipped_capital,
                "positions_skipped_slots": r.positions_skipped_slots,
                "peak_concurrent": r.peak_concurrent,
            }
            for i, r in enumerate(board.results, start=1)
        ],
    }


def persist(boards: dict[str, Leaderboard], results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "boards": {m: _board_dict(b) for m, b in boards.items()}}

    json_path = results_dir / f"tournament_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2))

    md_path = results_dir / f"tournament_{stamp}.md"
    md_path.write_text("\n\n".join(render_leaderboard(b) for b in boards.values()) + "\n")

    latest = results_dir / "latest.json"
    latest.write_text(json.dumps(payload, indent=2))
    return json_path
