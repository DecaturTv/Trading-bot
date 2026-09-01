"""Forex strategy research on 10y of daily OANDA candles (68 pairs).

Tests the edges the literature actually supports, which the live intraday
technical loop does not implement:
  - time-series (absolute) momentum: long if own trailing return > 0
  - cross-sectional momentum: long top-K / short bottom-K by trailing return
  - short-term reversal: fade the last N days

Reports annualised return / vol / Sharpe / max DD / hit rate per config, net
of a round-trip spread haircut. No look-ahead: signal uses data through t-1,
return realised over [t, t+H].

    ./.venv/bin/python _scratch_forex_daily_research.py
"""

from __future__ import annotations

import asyncio

import asyncpg
import numpy as np
import pandas as pd

DB = "postgresql://trading_bot:trading_bot@localhost:5432/trading_bot"
TF = "D"
ANN = 252
COST_BPS = 2.0  # round-trip spread haircut per pair per rebalance, in bps of notional

# clean USD-numeraire subset — one currency vs USD, minimal cross-holding overlap
USD_PAIRS = [
    "EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD", "USD_JPY", "USD_CHF", "USD_CAD",
    "USD_NOK", "USD_SEK", "USD_MXN", "USD_ZAR", "USD_TRY", "USD_PLN", "USD_HUF",
    "USD_SGD", "USD_CZK", "USD_CNH",
]


def load_closes() -> pd.DataFrame:
    async def _fetch():
        con = await asyncpg.connect(DB)
        try:
            return await con.fetch(
                "select symbol, ts::date as d, close from bars where timeframe=$1 "
                "and symbol ~ '^[A-Z]{3}_[A-Z]{3}$' order by d",
                TF,
            )
        finally:
            await con.close()

    recs = asyncio.run(_fetch())
    df = pd.DataFrame(recs, columns=["symbol", "d", "close"])
    wide = df.pivot(index="d", columns="symbol", values="close").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide


def stats(daily_ret: pd.Series, label: str, n_rebal: int) -> dict:
    daily_ret = daily_ret.dropna()
    if len(daily_ret) < ANN:
        return {}
    cum = (1 + daily_ret).cumprod()
    years = len(daily_ret) / ANN
    cagr = cum.iloc[-1] ** (1 / years) - 1
    vol = daily_ret.std() * np.sqrt(ANN)
    sharpe = (daily_ret.mean() * ANN) / vol if vol > 0 else 0.0
    dd = (cum / cum.cummax() - 1).min()
    monthly = (1 + daily_ret).resample("ME").prod() - 1
    return {
        "label": label,
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "maxdd": dd,
        "hit_mo": (monthly > 0).mean(),
        "n_days": len(daily_ret),
        "rebals": n_rebal,
    }


def run_ts_momentum(closes: pd.DataFrame, lookback: int, hold: int, cost_bps: float) -> pd.Series:
    """Long pairs with positive trailing `lookback`-day return, short the rest,
    equal weight, rebalanced every `hold` days."""
    rets = closes.pct_change()
    signal = np.sign(closes.pct_change(lookback))
    # only rebalance every `hold` days: forward-fill the signal between rebalances
    reb_idx = signal.index[lookback::hold]
    pos = signal.reindex(reb_idx).reindex(closes.index).ffill()
    pos = pos.div(pos.abs().sum(axis=1), axis=0)  # equal risk, gross 1.0
    strat = (pos.shift(1) * rets).sum(axis=1)
    turnover = pos.diff().abs().sum(axis=1).fillna(0.0)
    strat -= turnover * cost_bps / 1e4
    return strat


def run_xs_momentum(closes: pd.DataFrame, lookback: int, hold: int, k: int, cost_bps: float) -> pd.Series:
    """Long top-k / short bottom-k pairs by trailing `lookback`-day return."""
    rets = closes.pct_change()
    mom = closes.pct_change(lookback)
    reb_idx = mom.index[lookback::hold]

    def to_weights(row: pd.Series) -> pd.Series:
        r = row.dropna()
        if len(r) < 2 * k:
            return pd.Series(0.0, index=row.index)
        ranked = r.sort_values()
        w = pd.Series(0.0, index=row.index)
        w[ranked.index[-k:]] = 1.0 / k
        w[ranked.index[:k]] = -1.0 / k
        return w

    pos = mom.reindex(reb_idx).apply(to_weights, axis=1).reindex(closes.index).ffill()
    strat = (pos.shift(1) * rets).sum(axis=1)
    turnover = pos.diff().abs().sum(axis=1).fillna(0.0)
    strat -= turnover * cost_bps / 1e4
    return strat


def run_reversal(closes: pd.DataFrame, lookback: int, hold: int, cost_bps: float) -> pd.Series:
    rets = closes.pct_change()
    signal = -np.sign(closes.pct_change(lookback))
    reb_idx = signal.index[lookback::hold]
    pos = signal.reindex(reb_idx).reindex(closes.index).ffill()
    pos = pos.div(pos.abs().sum(axis=1), axis=0)
    strat = (pos.shift(1) * rets).sum(axis=1)
    turnover = pos.diff().abs().sum(axis=1).fillna(0.0)
    strat -= turnover * cost_bps / 1e4
    return strat


def main():
    closes = load_closes()
    print(f"loaded {closes.shape[1]} pairs, {closes.shape[0]} daily bars, "
          f"{closes.index.min().date()} -> {closes.index.max().date()}\n")

    universes = {"ALL_68": closes, "USD_17": closes[[c for c in USD_PAIRS if c in closes.columns]]}
    rows = []

    for uname, U in universes.items():
        U = U.dropna(how="all").ffill(limit=3)
        for lb in (21, 63, 126, 252):
            for hold in (5, 21, 63):
                s = run_ts_momentum(U, lb, hold, COST_BPS)
                r = stats(s, f"TSmom  {uname:7} lb={lb:>3} hold={hold:>2}", len(s.index[lb::hold]))
                if r:
                    rows.append(r)
        for lb in (21, 63, 126, 252):
            for hold in (5, 21, 63):
                for k in (3, 5):
                    if U.shape[1] < 2 * k:
                        continue
                    s = run_xs_momentum(U, lb, hold, k, COST_BPS)
                    r = stats(s, f"XSmom  {uname:7} lb={lb:>3} hold={hold:>2} k={k}", len(s.index[lb::hold]))
                    if r:
                        rows.append(r)
        for lb in (3, 5, 10, 21):
            for hold in (1, 3, 5):
                s = run_reversal(U, lb, hold, COST_BPS)
                r = stats(s, f"Revers {uname:7} lb={lb:>3} hold={hold:>2}", len(s.index[lb::hold]))
                if r:
                    rows.append(r)

    res = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    pd.set_option("display.width", 200, "display.max_rows", 200)
    fmt = res.copy()
    for c in ("cagr", "vol", "maxdd", "hit_mo"):
        fmt[c] = (fmt[c] * 100).round(1)
    fmt["sharpe"] = fmt["sharpe"].round(2)
    print(fmt[["label", "cagr", "vol", "sharpe", "maxdd", "hit_mo", "n_days", "rebals"]].to_string(index=False))
    print("\ncagr/vol/maxdd/hit_mo in %. Sharpe = ann. mean / ann. vol, rf=0, net of "
          f"{COST_BPS}bps round-trip/pair/rebalance.")
    print("\n=== TOP 10 by Sharpe ===")
    for _, r in res.head(10).iterrows():
        print(f"  {r['label']}  ->  CAGR {r['cagr']*100:+.1f}%  Sharpe {r['sharpe']:.2f}  maxDD {r['maxdd']*100:.0f}%")

    # --- walk-forward sanity on the headline configs: does 12m XS momentum
    #     hold up out-of-sample, or is the full-period Sharpe a fit? ---
    print("\n=== WALK-FORWARD (split at midpoint) — 12-month XS momentum ===")
    for uname, U in universes.items():
        Uf = U.dropna(how="all").ffill(limit=3)
        for k in (3, 5):
            s = run_xs_momentum(Uf, 252, 63, k, COST_BPS).dropna()
            mid = len(s) // 2
            h1, h2 = s.iloc[:mid], s.iloc[mid:]
            def sh(x):
                v = x.std() * np.sqrt(ANN)
                return (x.mean() * ANN / v) if v > 0 else 0.0
            def cg(x):
                return (1 + x).prod() ** (ANN / len(x)) - 1
            print(f"  {uname:7} k={k}  1H[{s.index[0].date()}..{s.index[mid].date()}] "
                  f"Sharpe {sh(h1):+.2f} CAGR {cg(h1)*100:+.1f}%   |   "
                  f"2H[{s.index[mid].date()}..{s.index[-1].date()}] Sharpe {sh(h2):+.2f} CAGR {cg(h2)*100:+.1f}%")


if __name__ == "__main__":
    main()
