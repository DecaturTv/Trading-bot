from collections.abc import Sequence

import numpy as np
import pandas as pd

from .models import MACDResult
from .moving_averages import ema


def rsi(values: Sequence[float], period: int = 14) -> list[float]:
    """Wilder's RSI (Wilder smoothing, i.e. ewm with alpha = 1/period)."""
    if period < 1:
        raise ValueError("period must be >= 1")
    series = pd.Series(values, dtype=float)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    result = pd.Series(np.nan, index=series.index)
    both_flat = (avg_gain == 0) & (avg_loss == 0)
    no_losses = (avg_loss == 0) & ~both_flat
    normal = ~both_flat & ~no_losses

    result[both_flat] = 50.0  # no price movement at all: neutral, not "maximally overbought"
    result[no_losses] = 100.0
    rs = avg_gain[normal] / avg_loss[normal]
    result[normal] = 100 - (100 / (1 + rs))

    return result.tolist()


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> MACDResult:
    if fast >= slow:
        raise ValueError("fast period must be less than slow period")
    macd_line = pd.Series(ema(values, fast)) - pd.Series(ema(values, slow))
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return MACDResult(
        macd_line=macd_line.tolist(),
        signal_line=signal_line.tolist(),
        histogram=histogram.tolist(),
    )
