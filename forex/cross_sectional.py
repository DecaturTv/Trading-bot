"""Cross-sectional FX momentum — the strategy the 2026-09-01 research
(_scratch_forex_daily_research.py, [[trading_platform_forex_research]]) found
to actually carry an edge: rank every tradeable pair by trailing ~12-month
return, hold an equal-weight long/short book of the top/bottom K, rebalance
quarterly. Sharpe ~0.8 over 10y, positive in both walk-forward halves —
unlike the intraday per-pair technical loop in dashboard/forex_loop.py, which
every tournament preset lost money on.

Pure functions only: no DB, no broker, no clock. dashboard/forex_xsmom_loop.py
wires these to daily bars, the OANDA adapter and the schedule.
"""

from __future__ import annotations

from dataclasses import dataclass

from decision_engine.models import TradeDirection


@dataclass(frozen=True)
class TargetLeg:
    pair: str
    direction: TradeDirection  # BULLISH = long the base currency, BEARISH = short it
    weight: float              # fraction of gross book notional, always positive
    score: float               # trailing return that earned it the slot (for logging)


def momentum_scores(closes_by_pair: dict[str, list[float]], lookback_bars: int) -> dict[str, float]:
    """Trailing simple return over the last `lookback_bars` bars, per pair.

    A pair needs at least lookback_bars + 1 closes; pairs with less history
    (or a non-positive reference price) are dropped rather than guessed at.
    """
    if lookback_bars < 1:
        raise ValueError("lookback_bars must be >= 1")
    scores: dict[str, float] = {}
    for pair, closes in closes_by_pair.items():
        if len(closes) < lookback_bars + 1:
            continue
        ref = closes[-1 - lookback_bars]
        last = closes[-1]
        if ref <= 0 or last <= 0:
            continue
        scores[pair] = last / ref - 1.0
    return scores


def target_book(scores: dict[str, float], top_k: int) -> list[TargetLeg]:
    """Long the `top_k` highest-scoring pairs, short the `top_k` lowest,
    equal weight 1/(2*top_k) each. Returns [] if fewer than 2*top_k pairs
    have a score (can't form a balanced book)."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    if len(scores) < 2 * top_k:
        return []
    ranked = sorted(scores.items(), key=lambda kv: kv[1])
    weight = 1.0 / (2 * top_k)
    shorts = ranked[:top_k]
    longs = ranked[-top_k:]
    legs = [TargetLeg(p, TradeDirection.BEARISH, weight, s) for p, s in shorts]
    legs += [TargetLeg(p, TradeDirection.BULLISH, weight, s) for p, s in longs]
    return legs


def leg_units(
    equity: float,
    gross_leverage: float,
    leg: TargetLeg,
    mid_price: float,
    quote_to_account_rate: float,
) -> int:
    """OANDA units (of the base currency) for one leg.

    Target account-currency notional for the leg = equity * gross_leverage *
    leg.weight. One base-currency unit is worth mid_price (quote ccy per base)
    * quote_to_account_rate (account ccy per quote ccy) in account terms, so
    units = notional / that. Rounded toward zero; can be 0 for a tiny account.
    """
    if equity <= 0 or gross_leverage <= 0:
        raise ValueError("equity and gross_leverage must be positive")
    if mid_price <= 0 or quote_to_account_rate <= 0:
        raise ValueError("mid_price and quote_to_account_rate must be positive")
    notional = equity * gross_leverage * leg.weight
    unit_value_account_ccy = mid_price * quote_to_account_rate
    return int(notional / unit_value_account_ccy)


def rebalance_plan(
    targets: list[TargetLeg],
    current_pairs: set[str],
) -> tuple[list[str], set[str]]:
    """Full-rebuild reconcile: every currently-held xsmom pair is closed and
    the new target book opened fresh. Turnover 4x/year is already inside the
    research's 2bps cost haircut, and a clean rebuild sidesteps unit-drift and
    partial-fill bookkeeping.

    Returns (pairs_to_close, pairs_to_open).
    """
    target_pairs = {leg.pair for leg in targets}
    return sorted(current_pairs), target_pairs
