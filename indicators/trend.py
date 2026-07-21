from collections.abc import Sequence

from broker.models import Bar

from .models import SuperTrendResult
from .volatility import atr


def supertrend(bars: Sequence[Bar], period: int = 10, multiplier: float = 3.0) -> SuperTrendResult:
    """SuperTrend: band-flip logic is path-dependent (each bar depends on the
    previous bar's final bands), so unlike the other indicators this can't be
    vectorized — it's a straightforward sequential pass."""
    if period < 1:
        raise ValueError("period must be >= 1")

    n = len(bars)
    atr_values = atr(bars, period)

    trend: list[float] = [float("nan")] * n
    direction: list[int] = [0] * n
    final_upper: list[float] = [float("nan")] * n
    final_lower: list[float] = [float("nan")] * n

    for i in range(n):
        if atr_values[i] != atr_values[i]:  # NaN: still in ATR warm-up
            continue

        hl2 = (bars[i].high + bars[i].low) / 2
        basic_upper = hl2 + multiplier * atr_values[i]
        basic_lower = hl2 - multiplier * atr_values[i]

        prev_final_upper = final_upper[i - 1] if i > 0 else float("nan")
        prev_final_lower = final_lower[i - 1] if i > 0 else float("nan")
        prev_close = bars[i - 1].close if i > 0 else None

        if prev_final_upper != prev_final_upper:
            final_upper[i] = basic_upper
        elif basic_upper < prev_final_upper or prev_close > prev_final_upper:
            final_upper[i] = basic_upper
        else:
            final_upper[i] = prev_final_upper

        if prev_final_lower != prev_final_lower:
            final_lower[i] = basic_lower
        elif basic_lower > prev_final_lower or prev_close < prev_final_lower:
            final_lower[i] = basic_lower
        else:
            final_lower[i] = prev_final_lower

        prev_trend = trend[i - 1] if i > 0 else float("nan")
        close = bars[i].close

        if prev_trend != prev_trend or prev_trend == prev_final_upper:
            if close <= final_upper[i]:
                trend[i] = final_upper[i]
                direction[i] = -1
            else:
                trend[i] = final_lower[i]
                direction[i] = 1
        else:  # prev_trend == prev_final_lower
            if close >= final_lower[i]:
                trend[i] = final_lower[i]
                direction[i] = 1
            else:
                trend[i] = final_upper[i]
                direction[i] = -1

    return SuperTrendResult(trend=trend, direction=direction)
