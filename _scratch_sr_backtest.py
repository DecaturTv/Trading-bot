"""Exploratory backtest: does a support/resistance bounce/rejection strategy
beat what's currently live (WeightedFactorModel momentum/trend/macd mix)?

Not wired into decision_engine -- this is throwaway research to answer one
question before touching anything live. See project memory: live equities is
19% win rate / -$1,332 and breakout options is 0/7 / -$491.50 over the last
3-4 trading days (2026-09-01 restart onward).

Method (all causal -- no lookahead):
  1. Detect swing highs/lows with a k-bar fractal (confirmed k bars after
     they form, same as a live scanner would see them).
  2. Cluster nearby swings into support/resistance levels, keep only levels
     touched >=2 times in the trailing lookback window.
  3. Entry: price pierces a level intrabar (low/high within touch_tol) and
     closes back on the "right" side of it (bounce off support = long,
     rejection at resistance = short).
  4. Stop: just beyond the level (ATR-scaled buffer). Target: the next level
     in the trade's direction; skip the trade if that doesn't clear
     min_reward_r. No target level found -> fall back to a fixed R multiple.
  5. Max hold caps how long a trade can ride before a flat close.

Reports win rate / avg win / avg loss / expectancy in R (position-size
independent) plus a dollar estimate using the same ~$70 average risk-per-
trade the live stop-losses have been realizing.
"""
import asyncio
import os
from dataclasses import dataclass

import asyncpg
import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

TIMEFRAME = "5Min"
PIVOT_K = 5                # bars each side to confirm a swing point
LEVEL_LOOKBACK_BARS = 780  # ~10 trading days of 5Min bars
CLUSTER_TOL = 0.0025       # group swings within 0.25% into one level
MIN_TOUCHES = 2            # level must have been touched >=2x to count
TOUCH_TOL = 0.0015         # price must come within 0.15% of a level to react
ATR_PERIOD = 14
STOP_ATR_MULT = 0.5        # stop = level +/- 0.5*ATR
MIN_REWARD_R = 1.5         # skip trade if next level doesn't clear 1.5R
FALLBACK_TARGET_R = 2.0    # used when no level exists in trade direction
MAX_HOLD_BARS = 234        # ~3 trading days of 5Min bars
RISK_PER_TRADE = 70.0      # $ sizing for the dollar estimate, matches live avg stop size


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry: float
    stop: float
    target: float
    exit_price: float
    r_multiple: float
    exit_reason: str


async def fetch_universe(pool: asyncpg.Pool) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT symbol FROM bars WHERE timeframe=$1 GROUP BY symbol HAVING count(*) >= $2",
            TIMEFRAME, LEVEL_LOOKBACK_BARS + 200,
        )
    return [r["symbol"] for r in rows]


async def fetch_bars(pool: asyncpg.Pool, symbol: str) -> pd.DataFrame:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol=$1 AND timeframe=$2 ORDER BY ts",
            symbol, TIMEFRAME,
        )
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def swing_points(df: pd.DataFrame, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Returns boolean arrays (is_swing_high, is_swing_low), confirmed (True
    only becomes visible k bars later -- the caller must look k bars back)."""
    high, low = df["high"].values, df["low"].values
    n = len(df)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    for i in range(k, n - k):
        window_h = high[i - k : i + k + 1]
        window_l = low[i - k : i + k + 1]
        if high[i] == window_h.max() and (window_h == high[i]).sum() == 1:
            is_high[i] = True
        if low[i] == window_l.min() and (window_l == low[i]).sum() == 1:
            is_low[i] = True
    return is_high, is_low


def cluster_levels(prices: list[float], tol: float) -> list[tuple[float, int]]:
    """Groups nearby swing prices into (level_price, touch_count), sorted by price."""
    if not prices:
        return []
    prices = sorted(prices)
    clusters: list[list[float]] = [[prices[0]]]
    for p in prices[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [(float(np.mean(c)), len(c)) for c in clusters]


def simulate_symbol(symbol: str, df: pd.DataFrame) -> list[Trade]:
    if len(df) < LEVEL_LOOKBACK_BARS + 2 * PIVOT_K + 10:
        return []

    df = df.reset_index(drop=True)
    df["atr"] = atr(df, ATR_PERIOD)
    is_high, is_low = swing_points(df, PIVOT_K)

    trades: list[Trade] = []
    position = None  # dict with direction/entry/stop/target/entry_idx

    n = len(df)
    for i in range(LEVEL_LOOKBACK_BARS, n):
        row = df.iloc[i]
        if pd.isna(row["atr"]) or row["atr"] <= 0:
            continue

        if position is not None:
            bars_held = i - position["entry_idx"]
            hit_stop = (
                row["low"] <= position["stop"] if position["direction"] == "long" else row["high"] >= position["stop"]
            )
            hit_target = (
                row["high"] >= position["target"] if position["direction"] == "long" else row["low"] <= position["target"]
            )
            if hit_stop and hit_target:
                # both touched in the same bar -- assume the worse outcome (stop) for a conservative estimate
                exit_price, reason = position["stop"], "stop"
            elif hit_stop:
                exit_price, reason = position["stop"], "stop"
            elif hit_target:
                exit_price, reason = position["target"], "target"
            elif bars_held >= MAX_HOLD_BARS:
                exit_price, reason = row["close"], "max_hold"
            else:
                continue

            risk = abs(position["entry"] - position["stop"])
            raw = (exit_price - position["entry"]) if position["direction"] == "long" else (position["entry"] - exit_price)
            trades.append(
                Trade(
                    symbol=symbol, direction=position["direction"], entry_ts=df["ts"].iloc[position["entry_idx"]],
                    exit_ts=row["ts"], entry=position["entry"], stop=position["stop"], target=position["target"],
                    exit_price=exit_price, r_multiple=raw / risk if risk > 0 else 0.0, exit_reason=reason,
                )
            )
            position = None
            continue

        # only form levels from swings confirmed as of this bar (swing at j needs j+k <= i-1)
        confirm_end = i - PIVOT_K
        if confirm_end <= LEVEL_LOOKBACK_BARS:
            continue
        window_start = max(0, confirm_end - LEVEL_LOOKBACK_BARS)
        highs_in_window = df["high"].values[window_start:confirm_end][is_high[window_start:confirm_end]]
        lows_in_window = df["low"].values[window_start:confirm_end][is_low[window_start:confirm_end]]
        resistances = [lvl for lvl, touches in cluster_levels(list(highs_in_window), CLUSTER_TOL) if touches >= MIN_TOUCHES]
        supports = [lvl for lvl, touches in cluster_levels(list(lows_in_window), CLUSTER_TOL) if touches >= MIN_TOUCHES]
        if not resistances and not supports:
            continue

        price_atr = row["atr"]

        # bounce off support -> long
        touched_support = [lvl for lvl in supports if row["low"] <= lvl * (1 + TOUCH_TOL) and row["close"] > lvl]
        if touched_support:
            level = max(touched_support)  # nearest support below/at price
            entry = row["close"]
            stop = level - STOP_ATR_MULT * price_atr
            risk = entry - stop
            if risk > 0:
                targets_above = [r for r in resistances if r > entry]
                target = min(targets_above) if targets_above else entry + FALLBACK_TARGET_R * risk
                reward_r = (target - entry) / risk
                if reward_r >= MIN_REWARD_R:
                    position = {"direction": "long", "entry": entry, "stop": stop, "target": target, "entry_idx": i}
                    continue

        # rejection at resistance -> short
        touched_resistance = [lvl for lvl in resistances if row["high"] >= lvl * (1 - TOUCH_TOL) and row["close"] < lvl]
        if touched_resistance:
            level = min(touched_resistance)
            entry = row["close"]
            stop = level + STOP_ATR_MULT * price_atr
            risk = stop - entry
            if risk > 0:
                targets_below = [s for s in supports if s < entry]
                target = max(targets_below) if targets_below else entry - FALLBACK_TARGET_R * risk
                reward_r = (entry - target) / risk
                if reward_r >= MIN_REWARD_R:
                    position = {"direction": "short", "entry": entry, "stop": stop, "target": target, "entry_idx": i}

    return trades


async def main():
    pool = await asyncpg.create_pool(DATABASE_URL)
    symbols = await fetch_universe(pool)
    print(f"universe: {len(symbols)} symbols with >= {LEVEL_LOOKBACK_BARS + 200} {TIMEFRAME} bars")

    all_trades: list[Trade] = []
    for idx, symbol in enumerate(symbols):
        df = await fetch_bars(pool, symbol)
        trades = simulate_symbol(symbol, df)
        all_trades.extend(trades)
        if (idx + 1) % 20 == 0:
            print(f"  ...{idx + 1}/{len(symbols)} symbols, {len(all_trades)} trades so far")

    await pool.close()

    if not all_trades:
        print("no trades generated -- params too strict for available history")
        return

    r_values = np.array([t.r_multiple for t in all_trades])
    wins = r_values[r_values > 0]
    losses = r_values[r_values <= 0]
    win_rate = len(wins) / len(r_values)
    avg_win_r = wins.mean() if len(wins) else 0.0
    avg_loss_r = losses.mean() if len(losses) else 0.0
    expectancy_r = r_values.mean()
    profit_factor = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float("inf")

    by_reason = pd.Series([t.exit_reason for t in all_trades]).value_counts()
    by_symbol_pnl = pd.DataFrame([(t.symbol, t.r_multiple) for t in all_trades], columns=["symbol", "r"]).groupby("symbol")["r"].sum().sort_values()

    print("\n=== S/R backtest results ===")
    print(f"trades: {len(all_trades)} across {by_symbol_pnl.shape[0]} symbols")
    print(f"win rate: {win_rate:.1%}  (wins={len(wins)} losses={len(losses)})")
    print(f"avg win: {avg_win_r:.2f}R   avg loss: {avg_loss_r:.2f}R")
    print(f"expectancy: {expectancy_r:.3f}R/trade   profit factor: {profit_factor:.2f}")
    print(f"exit reasons: {dict(by_reason)}")
    print(f"\ndollar estimate at ${RISK_PER_TRADE:.0f} risk/trade (matches live avg stop size):")
    print(f"  total: ${expectancy_r * RISK_PER_TRADE * len(all_trades):,.2f}")
    print(f"  per trade: ${expectancy_r * RISK_PER_TRADE:,.2f}")
    print(f"\nworst 5 symbols (sum R): \n{by_symbol_pnl.head(5)}")
    print(f"\nbest 5 symbols (sum R): \n{by_symbol_pnl.tail(5)}")

    out_path = "/root/trading-bot/_scratch_sr_trades.csv"
    pd.DataFrame([t.__dict__ for t in all_trades]).to_csv(out_path, index=False)
    print(f"\nfull trade log: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
