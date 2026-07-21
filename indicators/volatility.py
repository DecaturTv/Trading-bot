from collections.abc import Sequence

import pandas as pd

from broker.models import Bar


def atr(bars: Sequence[Bar], period: int = 14) -> list[float]:
    """Average True Range using Wilder smoothing (ewm with alpha = 1/period)."""
    if period < 1:
        raise ValueError("period must be >= 1")
    if not bars:
        return []

    highs = pd.Series([b.high for b in bars], dtype=float)
    lows = pd.Series([b.low for b in bars], dtype=float)
    closes = pd.Series([b.close for b in bars], dtype=float)
    prev_close = closes.shift(1)

    # Row 0 has no prev_close, so the two prev_close-based candidates are NaN;
    # DataFrame.max(axis=1) skips them and falls back to high - low.
    true_range = pd.concat(
        [highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().tolist()
