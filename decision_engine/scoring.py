from collections.abc import Sequence

from broker.models import Bar
from congress.models import CongressTrade
from scanner.models import ScanHit

from .factors import (
    candlestick_factor,
    congress_factor,
    gap_factor,
    macd_factor,
    momentum_factor,
    trend_factor,
    unusual_volume_factor,
)
from .models import FactorScore, TradeDirection, TradeSignal

# Momentum-only as of 2026-08-21: a 39-config sweep (composite blends,
# individual factors alone, several confidence thresholds) replayed against
# 30 real days of 1Day bars for 116 symbols through a shared-$2,100-balance
# portfolio simulation (real position-count/exposure caps, one Kelly trade
# history) ranked momentum-only @ confidence 55 the most profitable
# (+177% over the window). The previous multi-factor blend (momentum 0.16,
# trend 0.20, macd 0.12, unusual_volume 0.12, gap 0.08, candlestick 0.12,
# congress 0.20) ranked well down the list under the same test. Caveat this
# decision was made with, and should be revisited against once more real
# trade history exists: with only 3-13 trades per config in that sample,
# most configs' results (including this one) were dominated by 1-2 outsized
# single trades rather than a statistically robust edge. See project memory.
DEFAULT_WEIGHTS = {
    "momentum": 1.0,
    "trend": 0.0,
    "macd": 0.0,
    "unusual_volume": 0.0,
    "gap": 0.0,
    "candlestick": 0.0,
    "congress": 0.0,
}

# Forex keeps the original multi-factor blend (not derived from
# DEFAULT_WEIGHTS -- deliberately decoupled so the 2026-08-21 equities
# reweight above doesn't silently change forex too, which wasn't part of
# that decision) with unusual_volume excluded. Replaying the 24
# fully-logged forex trades from 2026-07-23/24 against real OANDA candle
# history showed this factor is anti-correlated with outcome there -- it
# agreed with the trade's eventual direction on 9/9 losers and 0/4 winners.
# OANDA's "volume" is synthetic tick count, not real traded volume; a
# tick-volume spike on an hourly FX candle tends to mark a climax/exhaustion
# move, not the start of a breakout the way a real equity volume spike often
# does. Weight simply drops to 0 rather than being deleted from the dict --
# WeightedFactorModel already excludes zero-weight factors and renormalizes
# over what's left, the same mechanism used for a factor that's unavailable.
# See project
# memory on forex performance.
FOREX_WEIGHTS = {
    "momentum": 0.16,
    "trend": 0.20,
    "macd": 0.12,
    "unusual_volume": 0.0,
    "gap": 0.08,
    "candlestick": 0.12,
    "congress": 0.20,
}

# The "Breakout Hunter" preset from the strategy tournament, run live in
# parallel with DEFAULT_WEIGHTS on its own $5,000 paper account (see
# dashboard/breakout_loop.py). Volatility-event driven: gap + unusual-volume +
# candlestick only, low coverage floor (0.30). In the shared-capital portfolio
# tournament (real option quotes, tournament_20260829T012340Z) it returned
# +100.9% with a 70% win rate and was the most *executable* of the four
# presets — only 141 of its wanted entries had no real option quote at the
# 15-min mark (near-money short-DTE contracts are liquid), versus 24,756 for
# the nominal winner. Paired with confidence 58 / delta 0.15 / DTE 20 /
# Kelly 0.20 (constants in breakout_loop.py).
BREAKOUT_WEIGHTS = {
    "momentum": 0.0,
    "trend": 0.0,
    "macd": 0.0,
    "unusual_volume": 0.35,
    "gap": 0.40,
    "candlestick": 0.25,
    "congress": 0.0,
}

_FACTOR_FUNCTIONS = {
    "momentum": lambda bars, scan_hits, congress_trades, tracked_members: momentum_factor(bars),
    "trend": lambda bars, scan_hits, congress_trades, tracked_members: trend_factor(bars),
    "macd": lambda bars, scan_hits, congress_trades, tracked_members: macd_factor(bars),
    "unusual_volume": lambda bars, scan_hits, congress_trades, tracked_members: unusual_volume_factor(bars, scan_hits),
    "gap": lambda bars, scan_hits, congress_trades, tracked_members: gap_factor(scan_hits),
    "candlestick": lambda bars, scan_hits, congress_trades, tracked_members: candlestick_factor(bars),
    "congress": lambda bars, scan_hits, congress_trades, tracked_members: congress_factor(bars, congress_trades, tracked_members),
}


class WeightedFactorModel:
    """Combines factor scores into a single confidence + direction.

    Missing factors (insufficient bar history, no relevant scan hit) are
    dropped and the remaining weights re-normalized, rather than treated as
    neutral zero — a symbol with only 2 of 5 factors available shouldn't be
    penalized as if the other 3 actively disagreed. But if too little of the
    total configured weight is actually available, the score isn't meaningful
    enough to act on, so it falls back to NEUTRAL/zero confidence.
    """

    def __init__(self, weights: dict[str, float] | None = None, min_available_weight_fraction: float = 0.5):
        weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)
        unknown = set(weights) - set(_FACTOR_FUNCTIONS)
        if unknown:
            raise ValueError(f"unknown factor name(s): {sorted(unknown)}")
        self._weights = weights
        self._min_available_weight_fraction = min_available_weight_fraction

    def score(
        self,
        symbol: str,
        bars: Sequence[Bar],
        scan_hits: Sequence[ScanHit],
        confidence_threshold: float,
        congress_trades: Sequence[CongressTrade] = (),
        tracked_members: Sequence[str] = (),
    ) -> TradeSignal:
        factors: list[FactorScore] = []
        for name, weight in self._weights.items():
            if weight <= 0:
                continue
            value = _FACTOR_FUNCTIONS[name](bars, scan_hits, congress_trades, tracked_members)
            if value is None:
                continue
            factors.append(FactorScore(name=name, value=value, weight=weight))

        total_configured_weight = sum(w for w in self._weights.values() if w > 0)
        total_available_weight = sum(f.weight for f in factors)
        coverage = total_available_weight / total_configured_weight if total_configured_weight else 0.0

        if not factors or coverage < self._min_available_weight_fraction:
            return TradeSignal(
                symbol=symbol,
                direction=TradeDirection.NEUTRAL,
                confidence=0.0,
                factors=factors,
                meets_threshold=False,
            )

        weighted_value = sum(f.value * f.weight for f in factors) / total_available_weight
        confidence = abs(weighted_value) * 100

        if weighted_value > 0:
            direction = TradeDirection.BULLISH
        elif weighted_value < 0:
            direction = TradeDirection.BEARISH
        else:
            direction = TradeDirection.NEUTRAL

        return TradeSignal(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            factors=factors,
            meets_threshold=confidence >= confidence_threshold,
        )
