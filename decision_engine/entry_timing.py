"""Entry-timing guards for the live options entry loop.

Added 2026-09-29 after a review of 71 option round trips (09-01 to 09-29)
found the momentum options loop was mostly buying the top of a move that
had just happened. Its median entry sat at 0.93 of the prior hour's range
and 4+ ATR into the move; 74% of entries fired in the first 15 minutes
after the open; 85% stopped out within minutes. Most of those stops came
back afterwards: the direction was right and the timing was wrong.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from broker.models import Bar

from .models import TradeDirection

TIMEFRAME_DURATIONS = {
    "5Min": timedelta(minutes=5),
    "15Min": timedelta(minutes=15),
    "1Hour": timedelta(hours=1),
    "1Day": timedelta(days=1),
}


def drop_forming_bar(bars: Sequence[Bar], timeframe: str, now: datetime) -> list[Bar]:
    """Returns bars without the trailing still-open bar, if there is one.

    Ingestion stores the in-progress bar and upserts it as it fills in, so
    scoring it means scoring a partial bar. At 09:31 the "1Day" bar is just
    the opening gap, and a 5Min bar changes on every poll.
    """
    bars = list(bars)
    duration = TIMEFRAME_DURATIONS.get(timeframe)
    if bars and duration is not None and bars[-1].timestamp + duration > now:
        bars.pop()
    return bars


def chase_rejection(
    bars_5m: Sequence[Bar],
    direction: TradeDirection,
    now: datetime,
    max_extension_atr: float,
    max_range_position: float,
    lookback_bars: int = 12,
    atr_period: int = 14,
) -> str | None:
    """Returns why an entry would be chasing the move, or None if it isn't.

    Measured on the underlying's 5Min bars against the last lookback_bars
    completed bars (12 = one hour). Two tests, both measured in the trade's
    direction:
    - extension: how far price has already run over that window, in ATRs.
    - range position: where price sits in that window's high-low range,
      where 1.0 means the top for a call and the bottom for a put.
    Allows the entry (returns None) when there isn't enough data to judge.
    """
    if direction is TradeDirection.NEUTRAL or not bars_5m:
        return None
    completed = drop_forming_bar(bars_5m, "5Min", now)
    if len(completed) < max(lookback_bars, atr_period) + 1:
        return None
    price = bars_5m[-1].close  # latest print, including the forming bar

    atr = average_true_range(completed, atr_period)
    if atr is None or atr <= 0:
        return None

    sign = 1.0 if direction is TradeDirection.BULLISH else -1.0
    window = completed[-lookback_bars:]
    extension = sign * (price - completed[-lookback_bars - 1].close) / atr
    if extension > max_extension_atr:
        return f"already ran {extension:.1f} ATR in the last {lookback_bars} bars (max {max_extension_atr:.1f})"

    high = max(bar.high for bar in window)
    low = min(bar.low for bar in window)
    if high > low:
        position = (price - low) / (high - low)
        if direction is TradeDirection.BEARISH:
            position = 1.0 - position
        if position > max_range_position:
            return f"at {position:.2f} of the last {lookback_bars} bars' range (max {max_range_position:.2f})"
    return None


def average_true_range(bars: Sequence[Bar], period: int = 14) -> float | None:
    """Simple average of the last `period` true ranges; None without period+1 bars."""
    if len(bars) < period + 1:
        return None
    true_ranges = [
        max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        for prev, bar in zip(bars[-period - 1:-1], bars[-period:])
    ]
    return sum(true_ranges) / len(true_ranges)


def underlying_stop_level(
    bars_5m: Sequence[Bar], direction: TradeDirection, now: datetime, atr_multiple: float, atr_period: int = 14
) -> tuple[float, float] | None:
    """(entry underlying price, stop price) for an option entry, or None when
    there isn't enough 5Min history to size the stop.

    The stop sits atr_multiple 5Min ATRs beyond the latest price: below it
    for a call, above it for a put. A 2026-09-29 replay of 72 option trades
    against real option bars found stops on the underlying beat the -25%
    premium stop at every multiple tried (1.5-6 ATR, all within ~$300 of each
    other), so 3 is a middle value, not a tuned optimum.
    """
    if direction is TradeDirection.NEUTRAL or not bars_5m:
        return None
    atr = average_true_range(drop_forming_bar(bars_5m, "5Min", now), atr_period)
    if atr is None or atr <= 0:
        return None
    price = bars_5m[-1].close
    offset = atr_multiple * atr
    return price, (price - offset if direction is TradeDirection.BULLISH else price + offset)
