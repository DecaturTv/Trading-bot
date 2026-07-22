from collections.abc import Sequence
from dataclasses import dataclass

from broker.models import Bar


@dataclass(frozen=True)
class CandlestickPattern:
    name: str
    direction: float  # +1.0 bullish, -1.0 bearish
    strength: float  # 0-1, how strongly the bar(s) match the textbook shape


def _body(bar: Bar) -> float:
    return bar.close - bar.open


def _body_size(bar: Bar) -> float:
    return abs(_body(bar))


def _range(bar: Bar) -> float:
    return bar.high - bar.low


def _upper_wick(bar: Bar) -> float:
    return bar.high - max(bar.open, bar.close)


def _lower_wick(bar: Bar) -> float:
    return min(bar.open, bar.close) - bar.low


def _is_bullish(bar: Bar) -> bool:
    return bar.close > bar.open


def _is_bearish(bar: Bar) -> bool:
    return bar.close < bar.open


def _local_trend(bars: Sequence[Bar], lookback: int = 5) -> float:
    """Sign/magnitude of close-to-close drift over the window preceding the
    most recent bar — crude, dependency-free context for patterns that only
    mean something at the top/bottom of a trend (hammer, shooting star)."""
    if len(bars) < lookback + 1:
        return 0.0
    window = bars[-(lookback + 1) : -1]
    return window[-1].close - window[0].close


def detect_engulfing(bars: Sequence[Bar]) -> CandlestickPattern | None:
    if len(bars) < 2:
        return None
    prev, curr = bars[-2], bars[-1]
    if _range(curr) <= 0 or _body_size(prev) <= 0:
        return None
    if _is_bearish(prev) and _is_bullish(curr) and curr.open <= prev.close and curr.close >= prev.open:
        strength = min(1.0, _body_size(curr) / _body_size(prev) / 2)
        return CandlestickPattern("bullish_engulfing", 1.0, strength)
    if _is_bullish(prev) and _is_bearish(curr) and curr.open >= prev.close and curr.close <= prev.open:
        strength = min(1.0, _body_size(curr) / _body_size(prev) / 2)
        return CandlestickPattern("bearish_engulfing", -1.0, strength)
    return None


def detect_hammer_family(bars: Sequence[Bar]) -> CandlestickPattern | None:
    """Hammer (bullish, downtrend bottom) / hanging man (bearish, uptrend
    top) / inverted hammer (bullish, downtrend bottom) / shooting star
    (bearish, uptrend top) — all share a small body near one end of the
    range with a long wick on the other side; direction depends on which
    wick is long plus the preceding local trend."""
    if not bars:
        return None
    curr = bars[-1]
    rng = _range(curr)
    if rng <= 0:
        return None
    body = _body_size(curr)
    if body / rng > 0.35:
        return None

    trend = _local_trend(bars)
    lower, upper = _lower_wick(curr), _upper_wick(curr)

    if lower >= 2 * body and upper <= body:
        if trend < 0:
            return CandlestickPattern("hammer", 1.0, min(1.0, lower / rng))
        if trend > 0:
            return CandlestickPattern("hanging_man", -1.0, min(1.0, lower / rng))
    if upper >= 2 * body and lower <= body:
        if trend > 0:
            return CandlestickPattern("shooting_star", -1.0, min(1.0, upper / rng))
        if trend < 0:
            return CandlestickPattern("inverted_hammer", 1.0, min(1.0, upper / rng))
    return None


def detect_doji(bars: Sequence[Bar]) -> CandlestickPattern | None:
    """A doji itself signals indecision, not direction — read as a possible
    exhaustion of whatever local trend precedes it."""
    if not bars:
        return None
    curr = bars[-1]
    rng = _range(curr)
    if rng <= 0 or _body_size(curr) / rng > 0.1:
        return None
    trend = _local_trend(bars)
    if trend == 0:
        return None
    return CandlestickPattern("doji", -1.0 if trend > 0 else 1.0, 0.3)


def detect_star(bars: Sequence[Bar]) -> CandlestickPattern | None:
    """Morning star (bullish) / evening star (bearish): a big first candle,
    a small-bodied middle "star" candle, then a big third candle closing
    well into the first candle's body."""
    if len(bars) < 3:
        return None
    first, star, third = bars[-3], bars[-2], bars[-1]
    if _range(first) <= 0 or _range(third) <= 0:
        return None
    first_body = _body_size(first)
    if first_body <= 0 or _body_size(star) / max(_range(star), 1e-9) > 0.3:
        return None

    first_mid = (first.open + first.close) / 2
    if _is_bearish(first) and _is_bullish(third) and star.close < first.close and third.close > first_mid:
        strength = max(0.0, min(1.0, (third.close - first_mid) / first_body))
        return CandlestickPattern("morning_star", 1.0, strength)
    if _is_bullish(first) and _is_bearish(third) and star.close > first.close and third.close < first_mid:
        strength = max(0.0, min(1.0, (first_mid - third.close) / first_body))
        return CandlestickPattern("evening_star", -1.0, strength)
    return None


_DETECTORS = (detect_star, detect_engulfing, detect_hammer_family, detect_doji)


def detect_pattern(bars: Sequence[Bar]) -> CandlestickPattern | None:
    """Returns the first matching pattern, checked in priority order — the
    3-candle reversal patterns are the strongest signal when present, then
    engulfing, then the single-candle wick patterns, then doji last (weakest)."""
    for detector in _DETECTORS:
        pattern = detector(bars)
        if pattern is not None:
            return pattern
    return None
