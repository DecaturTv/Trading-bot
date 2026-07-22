from collections.abc import Sequence

from broker.models import Bar
from scanner.models import ScanHit

from .factors import candlestick_factor, gap_factor, macd_factor, momentum_factor, trend_factor, unusual_volume_factor
from .models import FactorScore, TradeDirection, TradeSignal

DEFAULT_WEIGHTS = {
    "momentum": 0.20,
    "trend": 0.25,
    "macd": 0.15,
    "unusual_volume": 0.15,
    "gap": 0.10,
    "candlestick": 0.15,
}

_FACTOR_FUNCTIONS = {
    "momentum": lambda bars, scan_hits: momentum_factor(bars),
    "trend": lambda bars, scan_hits: trend_factor(bars),
    "macd": lambda bars, scan_hits: macd_factor(bars),
    "unusual_volume": lambda bars, scan_hits: unusual_volume_factor(bars, scan_hits),
    "gap": lambda bars, scan_hits: gap_factor(scan_hits),
    "candlestick": lambda bars, scan_hits: candlestick_factor(bars),
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
    ) -> TradeSignal:
        factors: list[FactorScore] = []
        for name, weight in self._weights.items():
            if weight <= 0:
                continue
            value = _FACTOR_FUNCTIONS[name](bars, scan_hits)
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
