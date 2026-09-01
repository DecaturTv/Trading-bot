"""Focused forex knob sweep against the LIVE FOREX_WEIGHTS model.

The tournament's forex board only tests its 4 presets' weight vectors; live
forex uses decision_engine.scoring.FOREX_WEIGHTS, which is none of them. This
sweeps take_profit_r_multiple x stop_atr_multiplier x confidence_threshold on
that exact model over the same H1 window the tournament uses, and reports
per-config expectancy so "fix the 1:1 payoff" is a measured choice, not a guess.

    ./.venv/bin/python _scratch_forex_sweep.py [--days 150]
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from backtesting.forex_engine import ForexBacktestEngine
from backtesting.forex_models import ForexBacktestConfig
from config.settings import Settings
from data.bars_repository import BarsRepository
from data.database import Database
from decision_engine.scoring import FOREX_WEIGHTS
from decision_engine.scoring import WeightedFactorModel

TIMEFRAME = "H1"
PAIR_RE = r"^[A-Z]{3}_[A-Z]{3}$"
BANKROLL = 300.0
RISK_PCT = 0.02  # live FOREX_RISK_PCT_PER_TRADE

TP_GRID = [1.0, 1.5, 2.0, 2.5, 3.0]
ATR_GRID = [2.5]        # live value; keep the grid small — the engine is O(n^2)/pair
CONF_GRID = [85]        # live value
MIN_COVERAGE = 0.6  # live FOREX_MIN_COVERAGE_FRACTION


async def load_bars(days: int) -> dict[str, list]:
    db = Database.from_settings(Settings())
    await db.connect()
    try:
        rows = await db.pool.fetch(
            f"SELECT DISTINCT symbol FROM bars WHERE timeframe=$1 AND symbol ~ '{PAIR_RE}' ORDER BY symbol",
            TIMEFRAME,
        )
        pairs = [r["symbol"] for r in rows]
        repo = BarsRepository(db.pool)
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        out = {}
        for p in pairs:
            bars = await repo.get_bars(p, TIMEFRAME, start, now)
            if bars:
                out[p] = bars
        return out
    finally:
        await db.disconnect()


def run_config(bars_by_pair, conf, atr_mult, tp_r):
    model = WeightedFactorModel(weights=FOREX_WEIGHTS, min_available_weight_fraction=MIN_COVERAGE)
    config = ForexBacktestConfig(
        starting_equity=BANKROLL,
        confidence_threshold=conf,
        risk_pct_per_trade=RISK_PCT,
        stop_atr_multiplier=atr_mult,
        take_profit_r_multiple=tp_r,
        warmup_bars=60,
    )
    engine = ForexBacktestEngine(model, config)
    rs = []
    reasons = {}
    for pair, bars in bars_by_pair.items():
        if len(bars) < 60 + config.min_candles_for_signal + 2:
            continue
        for t in engine.run(pair, bars).trades:
            rs.append(t.r_multiple)
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    n = len(rs)
    if n == 0:
        return dict(n=0, wins=0, wr=0.0, avg_w=0.0, avg_l=0.0, total_r=0.0, exp_r=0.0, reasons=reasons)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    return dict(
        n=n,
        wins=len(wins),
        wr=len(wins) / n,
        avg_w=(sum(wins) / len(wins)) if wins else 0.0,
        avg_l=(sum(losses) / len(losses)) if losses else 0.0,
        total_r=sum(rs),
        exp_r=sum(rs) / n,
        reasons=reasons,
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=150)
    args = ap.parse_args()

    bars_by_pair = await load_bars(args.days)
    print(f"loaded {len(bars_by_pair)} pairs, {TIMEFRAME}, {args.days}d\n")
    print(f"model = live FOREX_WEIGHTS {FOREX_WEIGHTS}  coverage>={MIN_COVERAGE}  risk={RISK_PCT:.0%}\n")

    one_r = BANKROLL * RISK_PCT
    header = f"{'conf':>4} {'atr':>4} {'tp_R':>5} | {'trades':>6} {'win%':>6} {'avgW_R':>7} {'avgL_R':>7} {'tot_R':>8} {'exp_R':>7} | P&L $ (1R=${one_r:.2f})"
    print(header)
    print("-" * len(header))
    results = []
    for conf in CONF_GRID:
        for atr_mult in ATR_GRID:
            for tp_r in TP_GRID:
                r = run_config(bars_by_pair, conf, atr_mult, tp_r)
                pnl = r["total_r"] * BANKROLL * RISK_PCT
                results.append(((conf, atr_mult, tp_r), r, pnl))
                print(
                    f"{conf:>4} {atr_mult:>4.1f} {tp_r:>5.1f} | {r['n']:>6} {r['wr']*100:>5.1f}% "
                    f"{r['avg_w']:>7.2f} {r['avg_l']:>7.2f} {r['total_r']:>8.2f} {r['exp_r']:>7.3f} | "
                    f"${pnl:>+8.2f}  reasons={r['reasons']}",
                    flush=True,
                )
    print()
    ranked = sorted(results, key=lambda x: x[1]["total_r"], reverse=True)
    print("=== top 5 by total R ===")
    for (conf, atr_mult, tp_r), r, pnl in ranked[:5]:
        print(f"  conf={conf} atr={atr_mult} tp={tp_r}R  ->  {r['n']} trades, {r['wr']*100:.0f}% win, "
              f"exp {r['exp_r']:+.3f}R/trade, total {r['total_r']:+.1f}R (${pnl:+.2f}), reasons={r['reasons']}")
    print("\n=== current live config (conf=85, atr=2.5, tp=1.0) ===")
    for (conf, atr_mult, tp_r), r, pnl in results:
        if (conf, atr_mult, tp_r) == (85, 2.5, 1.0):
            print(f"  {r['n']} trades, {r['wr']*100:.0f}% win, exp {r['exp_r']:+.3f}R/trade, "
                  f"total {r['total_r']:+.1f}R (${pnl:+.2f}), reasons={r['reasons']}")


if __name__ == "__main__":
    asyncio.run(main())
