"""Validate dashboard/forex_xsmom_loop's logic against the research.

Drives the ACTUAL module functions (forex.cross_sectional.momentum_scores +
target_book) over 10y of daily closes, rebalancing every `hold` trading days,
and reports Sharpe / CAGR / maxDD + walk-forward halves. Should reproduce the
~0.78 Sharpe / +11% CAGR the free-standing research script found for
lb=252 / hold=63 / k=5 on ALL_68 — if it doesn't, the module and the
research have diverged.

    ./.venv/bin/python _scratch_forex_xsmom_backtest.py
"""

from __future__ import annotations

import asyncio

import asyncpg
import numpy as np
import pandas as pd

from decision_engine.models import TradeDirection
from forex.cross_sectional import momentum_scores, target_book

DB = "postgresql://trading_bot:trading_bot@localhost:5432/trading_bot"
ANN = 252
COST_BPS = 2.0
LOOKBACK = 252
HOLD = 63


def load_closes() -> pd.DataFrame:
    async def _fetch():
        con = await asyncpg.connect(DB)
        try:
            return await con.fetch(
                "select symbol, ts::date as d, close from bars where timeframe='D' "
                "and symbol ~ '^[A-Z]{3}_[A-Z]{3}$' order by d"
            )
        finally:
            await con.close()

    recs = asyncio.run(_fetch())
    df = pd.DataFrame(recs, columns=["symbol", "d", "close"])
    wide = df.pivot(index="d", columns="symbol", values="close").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide.ffill(limit=3)


def backtest(closes: pd.DataFrame, k: int) -> pd.Series:
    rets = closes.pct_change(fill_method=None)
    dates = closes.index
    weights = pd.DataFrame(0.0, index=dates, columns=closes.columns)

    reb_points = range(LOOKBACK, len(dates), HOLD)
    for i in reb_points:
        window = closes.iloc[: i + 1]
        closes_by_pair = {
            c: window[c].dropna().tolist()
            for c in window.columns
            if window[c].notna().sum() >= LOOKBACK + 1
        }
        scores = momentum_scores(closes_by_pair, LOOKBACK)
        legs = target_book(scores, k)
        if not legs:
            continue
        w = pd.Series(0.0, index=closes.columns)
        for leg in legs:
            w[leg.pair] = leg.weight if leg.direction is TradeDirection.BULLISH else -leg.weight
        end = min(i + HOLD, len(dates))
        weights.iloc[i:end] = w.values

    strat = (weights.shift(1) * rets).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(0.0)
    strat -= turnover * COST_BPS / 1e4
    return strat.dropna()


def report(s: pd.Series, label: str):
    cum = (1 + s).cumprod()
    years = len(s) / ANN
    cagr = cum.iloc[-1] ** (1 / years) - 1
    vol = s.std() * np.sqrt(ANN)
    sharpe = (s.mean() * ANN) / vol if vol > 0 else 0.0
    dd = (cum / cum.cummax() - 1).min()
    mid = len(s) // 2
    def sh(x):
        v = x.std() * np.sqrt(ANN)
        return (x.mean() * ANN / v) if v > 0 else 0.0
    print(f"{label}: CAGR {cagr*100:+.1f}%  vol {vol*100:.1f}%  Sharpe {sharpe:.2f}  maxDD {dd*100:.0f}%  "
          f"| WF 1H Sharpe {sh(s.iloc[:mid]):+.2f}  2H Sharpe {sh(s.iloc[mid:]):+.2f}  ({len(s)} days)")


def main():
    closes = load_closes()
    print(f"loaded {closes.shape[1]} pairs, {closes.shape[0]} daily bars, "
          f"{closes.index.min().date()} -> {closes.index.max().date()}")
    print(f"module path: forex.cross_sectional  lb={LOOKBACK} hold={HOLD} cost={COST_BPS}bps\n")
    for k in (3, 5):
        report(backtest(closes, k), f"xsmom k={k}")
    print("\nresearch reference (free-standing script, ALL_68): k=3 Sharpe 0.95 / +18% ; k=5 Sharpe 0.78 / +11%")


if __name__ == "__main__":
    main()
