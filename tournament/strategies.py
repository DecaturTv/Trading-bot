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

All four strategies share the 2026-08-28 risk baseline — -25% stop, scale out
half at +$20, 20% trailing on the remainder, force-close after 1 trading day
(the runner sets max_hold/scale_out; stop/target/trail are pinned equal in
every EquityKnobs below). The contest is purely which factor mix, delta, DTE,
and Kelly fraction earn the most under those fixed rules.
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

STRATEGIES: list[Strategy] = [MOMENTUM_RIDER, BALANCED_BLEND, TREND_FOLLOWER, BREAKOUT_HUNTER]


def by_name(name: str) -> Strategy:
    """Case-insensitive lookup, tolerant of surrounding whitespace."""
    key = name.strip().lower()
    for strategy in STRATEGIES:
        if strategy.name.lower() == key:
            return strategy
    raise KeyError(f"no strategy named {name!r}; choices: {[s.name for s in STRATEGIES]}")
