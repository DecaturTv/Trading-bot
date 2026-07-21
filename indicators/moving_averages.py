from collections.abc import Sequence

import pandas as pd


def sma(values: Sequence[float], period: int) -> list[float]:
    if period < 1:
        raise ValueError("period must be >= 1")
    return pd.Series(values, dtype=float).rolling(window=period).mean().tolist()


def ema(values: Sequence[float], period: int) -> list[float]:
    """Recursive EMA seeded from the first value (pandas ewm, adjust=False) —
    not the SMA-seeded variant some charting platforms use."""
    if period < 1:
        raise ValueError("period must be >= 1")
    series = pd.Series(values, dtype=float)
    return series.ewm(span=period, adjust=False, min_periods=period).mean().tolist()
