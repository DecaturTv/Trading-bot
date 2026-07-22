from collections.abc import Sequence


def iv_rank(current_iv: float, historical_ivs: Sequence[float]) -> float:
    """Where current_iv sits in the historical [min, max] range, as a 0-100 scale."""
    if not historical_ivs:
        raise ValueError("historical_ivs must not be empty")
    lo, hi = min(historical_ivs), max(historical_ivs)
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, (current_iv - lo) / (hi - lo) * 100))


def iv_percentile(current_iv: float, historical_ivs: Sequence[float]) -> float:
    """Percentage of historical observations strictly below current_iv."""
    if not historical_ivs:
        raise ValueError("historical_ivs must not be empty")
    below = sum(1 for iv in historical_ivs if iv < current_iv)
    return below / len(historical_ivs) * 100
