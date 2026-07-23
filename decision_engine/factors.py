from collections.abc import Sequence

from broker.models import Bar
from indicators.candlesticks import detect_pattern
from indicators.momentum import macd, rsi
from indicators.trend import supertrend
from indicators.volatility import atr
from scanner.models import ScanHit, ScanType


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def momentum_factor(bars: Sequence[Bar], period: int = 14) -> float | None:
    """RSI centered on 0: >50 bullish, <50 bearish, saturating to [-1, 1] at
    the RSI 80/20 overbought/oversold levels rather than the 100/0 extremes,
    which real market RSI rarely reaches."""
    closes = [b.close for b in bars]
    rsi_values = rsi(closes, period)
    if not rsi_values or rsi_values[-1] != rsi_values[-1]:  # NaN: still warming up
        return None
    return _clip((rsi_values[-1] - 50) / 30)


def macd_factor(bars: Sequence[Bar]) -> float | None:
    """MACD histogram sign/magnitude, normalized by its own recent range so the
    scale is comparable across symbols with very different price levels."""
    closes = [b.close for b in bars]
    histogram = macd(closes).histogram
    if not histogram or histogram[-1] != histogram[-1]:
        return None
    recent = [h for h in histogram[-20:] if h == h]
    scale = max((abs(h) for h in recent), default=0.0)
    if scale == 0:
        return 0.0
    return _clip(histogram[-1] / scale)


def trend_factor(bars: Sequence[Bar], period: int = 10, multiplier: float = 3.0) -> float | None:
    """SuperTrend direction, scaled by how many ATRs price sits from the trend
    line, saturating to 1.0 at 0.5 ATR of separation — SuperTrend trails price
    tightly, so a full ATR of separation is rare even in a strong trend."""
    result = supertrend(bars, period=period, multiplier=multiplier)
    if not result.direction or result.direction[-1] == 0:
        return None
    direction = result.direction[-1]
    trend_value = result.trend[-1]
    atr_values = atr(bars, period)
    latest_atr = atr_values[-1]
    close = bars[-1].close
    if latest_atr != latest_atr or latest_atr == 0:
        magnitude = 1.0
    else:
        magnitude = _clip(abs(close - trend_value) / (0.5 * latest_atr), 0.0, 1.0)
    return direction * magnitude


def unusual_volume_factor(bars: Sequence[Bar], scan_hits: Sequence[ScanHit]) -> float | None:
    """Direction comes from the latest bar's price move; magnitude from the scan hit's ratio."""
    hit = next((h for h in scan_hits if h.scan_type is ScanType.UNUSUAL_VOLUME), None)
    if hit is None or len(bars) < 2:
        return None
    direction = 1.0 if bars[-1].close >= bars[-2].close else -1.0
    magnitude = _clip(hit.score / (hit.score + 1), 0.0, 1.0)  # ratio -> (0, 1), asymptotic
    return direction * magnitude


def gap_factor(scan_hits: Sequence[ScanHit], full_scale_gap_pct: float = 0.10) -> float | None:
    """Gap % sign is the direction; magnitude scales to 1.0 at full_scale_gap_pct."""
    hit = next((h for h in scan_hits if h.scan_type is ScanType.GAP), None)
    if hit is None:
        return None
    gap_pct = hit.details.get("gap_pct", 0.0)
    return _clip(gap_pct / full_scale_gap_pct)


def candlestick_factor(bars: Sequence[Bar]) -> float | None:
    """Most recent candlestick reversal/continuation pattern (engulfing,
    hammer/hanging-man/shooting-star/inverted-hammer, doji, morning/evening
    star), signed by direction and scaled by how strongly the bars match the
    textbook shape."""
    pattern = detect_pattern(bars)
    if pattern is None:
        return None
    return _clip(pattern.direction * pattern.strength)
