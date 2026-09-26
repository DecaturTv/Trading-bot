"""The tournament competitors.

Each Strategy is a *preset* — a named bundle of knobs that already exist in the
platform (decision_engine factor weights + WeightedFactorModel coverage floor,
confidence threshold, trade-management exit rules, Kelly fraction for equities;
ATR stop / take-profit / risk-per-trade for forex). The tournament runner turns
each preset into a WeightedFactorModel + BacktestConfig / ForexBacktestConfig
and runs it through the unchanged engines.

`congress` is deliberately absent from every weight vector: neither backtest
engine passes disclosure data to WeightedFactorModel.score(), so a congress
weight would always be dropped-and-renormalized to nothing. Competing on a
factor that can't be scored here would just be noise.

The original four strategies share the 2026-08-28 risk baseline — -25% stop,
scale out half at +$20, 20% trailing on the remainder, force-close after 1
trading day (EquityKnobs.max_hold_trading_days/scale_out_fraction default to
that baseline so they need no explicit setting below). Among those four the
contest is purely which factor mix, delta, DTE, and Kelly fraction earn the
most under identical exit rules. Breakout Hunter — Let It Ride overrides the
hold-time shape instead, to answer a different question (see its own
docstring below).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityKnobs:
    """Per-strategy settings for the options BacktestEngine path."""

    confidence_threshold: float
    target_delta: float
    target_dte: int  # trading days
    stop_loss_pct: float
    profit_target_dollars: float
    trailing_stop_pct: float
    kelly_fraction: float
    # Hold-time/scale-out shape. Defaulted to the 2026-08-28 shared baseline
    # (force-close after 1 trading day, scale out half at the profit target)
    # so the four original strategies below need no changes -- override on a
    # new competitor to test a different hold-time shape, e.g. Breakout
    # Hunter — Let It Ride below, which tests riding a winner toward
    # expiration instead of forcing a same-day exit (see project memory on
    # the INTC trade that raised the question).
    max_hold_trading_days: int = 1
    scale_out_fraction: float = 0.5
    # None (default): the calendar cap above is unconditional, same as every
    # original strategy. Set it to let a position outlive the cap for as long
    # as a fresh re-score of the same signal still meets this confidence
    # floor and still agrees with the entry direction -- see
    # trade_management.models.TradeManagementConfig.conviction_hold_confidence_floor.
    conviction_hold_confidence_floor: float | None = None


@dataclass(frozen=True)
class ForexKnobs:
    """Per-strategy settings for the ForexBacktestEngine path."""

    confidence_threshold: float
    risk_pct_per_trade: float
    stop_atr_multiplier: float
    take_profit_r_multiple: float


@dataclass(frozen=True)
class Strategy:
    name: str
    blurb: str
    # Factor weights, same shape decision_engine.scoring.DEFAULT_WEIGHTS uses.
    # Only non-zero factors need listing; WeightedFactorModel drops the rest.
    weights: dict[str, float]
    # Minimum fraction of configured weight that must be available on a bar
    # for the score to count (WeightedFactorModel.min_available_weight_fraction).
    # Lower it for event-driven blends whose factors are individually sparse.
    min_coverage: float
    equities: EquityKnobs
    forex: ForexKnobs


# --- The lineup -------------------------------------------------------------
#
# Weight vectors are written un-normalized for readability; WeightedFactorModel
# normalizes over whatever is available at score time.

MOMENTUM_RIDER = Strategy(
    name="Momentum Rider",
    blurb="Pure RSI momentum — the current live DEFAULT_WEIGHTS. Lowest entry "
    "threshold of the four; smallest delta, shortest DTE.",
    weights={"momentum": 1.0},
    min_coverage=0.5,
    equities=EquityKnobs(
        confidence_threshold=55,
        target_delta=0.15,
        target_dte=25,
        stop_loss_pct=0.25,
        profit_target_dollars=20.0,
        trailing_stop_pct=0.20,
        kelly_fraction=0.25,
    ),
    forex=ForexKnobs(
        confidence_threshold=75,
        risk_pct_per_trade=0.02,
        stop_atr_multiplier=2.5,
        take_profit_r_multiple=1.0,
    ),
)

BALANCED_BLEND = Strategy(
    name="Balanced Blend",
    blurb="Diversified multi-factor blend (the pre-2026-08-21 weighting, congress "
    "dropped). No single factor dominates; mid threshold and DTE.",
    weights={
        "trend": 0.28,
        "momentum": 0.22,
        "macd": 0.17,
        "unusual_volume": 0.17,
        "candlestick": 0.17,
        "gap": 0.16,
    },
    min_coverage=0.5,
    equities=EquityKnobs(
        confidence_threshold=62,
        target_delta=0.20,
        target_dte=30,
        stop_loss_pct=0.25,
        profit_target_dollars=20.0,
        trailing_stop_pct=0.20,
        kelly_fraction=0.25,
    ),
    forex=ForexKnobs(
        confidence_threshold=82,
        risk_pct_per_trade=0.02,
        stop_atr_multiplier=2.5,
        take_profit_r_multiple=1.5,
    ),
)

TREND_FOLLOWER = Strategy(
    name="Trend Follower",
    blurb="SuperTrend + MACD led, momentum as tiebreak. Highest entry threshold, "
    "widest delta, longest DTE, largest Kelly fraction.",
    weights={"trend": 0.45, "macd": 0.35, "momentum": 0.20},
    min_coverage=0.5,
    equities=EquityKnobs(
        confidence_threshold=68,
        target_delta=0.25,
        target_dte=40,
        stop_loss_pct=0.25,
        profit_target_dollars=20.0,
        trailing_stop_pct=0.20,
        kelly_fraction=0.30,
    ),
    forex=ForexKnobs(
        confidence_threshold=86,
        risk_pct_per_trade=0.025,
        stop_atr_multiplier=3.0,
        take_profit_r_multiple=2.5,
    ),
)

BREAKOUT_HUNTER = Strategy(
    name="Breakout Hunter",
    blurb="Gap + unusual-volume + candlestick. Trades volatility events only "
    "(lower coverage floor); smallest Kelly fraction, shortest DTE.",
    weights={"gap": 0.40, "unusual_volume": 0.35, "candlestick": 0.25},
    min_coverage=0.30,
    equities=EquityKnobs(
        confidence_threshold=58,
        target_delta=0.15,
        target_dte=20,
        stop_loss_pct=0.25,
        profit_target_dollars=20.0,
        trailing_stop_pct=0.20,
        kelly_fraction=0.20,
    ),
    forex=ForexKnobs(
        confidence_threshold=80,
        risk_pct_per_trade=0.015,
        stop_atr_multiplier=2.0,
        take_profit_r_multiple=1.0,
    ),
)

BREAKOUT_HUNTER_LET_IT_RIDE = Strategy(
    name="Breakout Hunter — Let It Ride",
    blurb="Same signal, delta, DTE, and Kelly fraction as Breakout Hunter -- "
    "isolates one question: does riding a winner toward expiration beat "
    "forcing a same-day exit? Prompted by the INTC breakout call "
    "(2026-08-31) that a close-order bug accidentally let ride 18 days into "
    "an option assignment for +$13,455 -- see project memory. profit_target_"
    "dollars is set far out of reach so the $20 scale-out never fires, and "
    "max_hold_trading_days is stretched to the DTE window instead of forcing "
    "a same-day close; the -25% stop and reversal-exit are unchanged, so a "
    "loser still gets cut early -- only a winner's hold time changes.",
    weights=BREAKOUT_HUNTER.weights,
    min_coverage=BREAKOUT_HUNTER.min_coverage,
    equities=EquityKnobs(
        confidence_threshold=BREAKOUT_HUNTER.equities.confidence_threshold,
        target_delta=BREAKOUT_HUNTER.equities.target_delta,
        target_dte=BREAKOUT_HUNTER.equities.target_dte,
        stop_loss_pct=BREAKOUT_HUNTER.equities.stop_loss_pct,
        # Effectively disabled: at this sizing (bankroll x 0.20 Kelly
        # fraction, single-digit contracts) a real dollar_gain never reaches
        # 7 figures, so the position never scales out early -- it rides
        # under the stop-loss/reversal/max-hold rules alone.
        profit_target_dollars=1_000_000.0,
        trailing_stop_pct=BREAKOUT_HUNTER.equities.trailing_stop_pct,
        kelly_fraction=BREAKOUT_HUNTER.equities.kelly_fraction,
        # ~20 calendar-day DTE is ~14 trading days; min_trading_days_before_
        # expiry=2 (set by the runner for every strategy) force-closes 2
        # trading days ahead of that regardless, so this is a generous
        # backstop rather than the binding constraint.
        max_hold_trading_days=14,
        scale_out_fraction=BREAKOUT_HUNTER.equities.scale_out_fraction,
    ),
    forex=BREAKOUT_HUNTER.forex,
)

BREAKOUT_HUNTER_CONVICTION_HOLD = Strategy(
    name="Breakout Hunter — Conviction Hold",
    blurb="Same signal, delta, DTE, Kelly fraction, and $20/0.20 profit-"
    "target/trailing-stop as Breakout Hunter -- the only difference is what "
    "happens once max_hold_trading_days (1) is reached. Instead of Let It "
    "Ride's blind calendar extension, the position is re-scored against the "
    "live model every single day it's held past that point: it only keeps "
    "riding for as long as that fresh score still clears the entry "
    "confidence floor AND still agrees with the direction it was opened on; "
    "the moment either fails, it's force-closed that same cycle, same as the "
    "1-day baseline would have done on day 1. A real stop-loss or a "
    "confirmed reversal still closes it regardless, exactly as in every "
    "other strategy here. This is the mechanism -- 'keep analyzing while "
    "we're in the trade, don't just fix a hold-time number' -- the INTC "
    "trade actually raised; Let It Ride is the naive version kept alongside "
    "it for comparison.",
    weights=BREAKOUT_HUNTER.weights,
    min_coverage=BREAKOUT_HUNTER.min_coverage,
    equities=EquityKnobs(
        confidence_threshold=BREAKOUT_HUNTER.equities.confidence_threshold,
        target_delta=BREAKOUT_HUNTER.equities.target_delta,
        target_dte=BREAKOUT_HUNTER.equities.target_dte,
        stop_loss_pct=BREAKOUT_HUNTER.equities.stop_loss_pct,
        profit_target_dollars=BREAKOUT_HUNTER.equities.profit_target_dollars,
        trailing_stop_pct=BREAKOUT_HUNTER.equities.trailing_stop_pct,
        kelly_fraction=BREAKOUT_HUNTER.equities.kelly_fraction,
        max_hold_trading_days=BREAKOUT_HUNTER.equities.max_hold_trading_days,  # 1 -- same baseline cap
        scale_out_fraction=BREAKOUT_HUNTER.equities.scale_out_fraction,
        # Same bar the entry model itself requires to open a position in the
        # first place -- "keep holding for as long as this would still
        # qualify as a fresh entry," not a looser or stricter bar.
        conviction_hold_confidence_floor=BREAKOUT_HUNTER.equities.confidence_threshold,
    ),
    forex=BREAKOUT_HUNTER.forex,
)

STRATEGIES: list[Strategy] = [
    MOMENTUM_RIDER, BALANCED_BLEND, TREND_FOLLOWER, BREAKOUT_HUNTER,
    BREAKOUT_HUNTER_LET_IT_RIDE, BREAKOUT_HUNTER_CONVICTION_HOLD,
]


def by_name(name: str) -> Strategy:
    """Case-insensitive lookup, tolerant of surrounding whitespace."""
    key = name.strip().lower()
    for strategy in STRATEGIES:
        if strategy.name.lower() == key:
            return strategy
    raise KeyError(f"no strategy named {name!r}; choices: {[s.name for s in STRATEGIES]}")
