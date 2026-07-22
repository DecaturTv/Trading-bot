from collections.abc import Sequence

from broker.models import Bar
from indicators.momentum import rsi

from .models import ScanHit, ScanType


def scan_unusual_volume(
    symbol: str, bars: Sequence[Bar], lookback: int = 20, threshold: float = 2.0
) -> ScanHit | None:
    """Flags a symbol whose latest bar's volume is `threshold`x its trailing average."""
    if len(bars) < lookback + 1:
        return None
    recent = bars[-(lookback + 1) : -1]
    avg_volume = sum(b.volume for b in recent) / lookback
    if avg_volume <= 0:
        return None
    latest_volume = bars[-1].volume
    ratio = latest_volume / avg_volume
    if ratio < threshold:
        return None
    return ScanHit(
        symbol=symbol,
        scan_type=ScanType.UNUSUAL_VOLUME,
        score=ratio,
        details={"latest_volume": latest_volume, "avg_volume": avg_volume, "ratio": ratio},
    )


def scan_gap(symbol: str, bars: Sequence[Bar], threshold_pct: float = 0.03) -> ScanHit | None:
    """Flags a symbol whose latest bar opened `threshold_pct` away from the prior close."""
    if len(bars) < 2:
        return None
    prev_close = bars[-2].close
    today_open = bars[-1].open
    if prev_close <= 0:
        return None
    gap_pct = (today_open - prev_close) / prev_close
    if abs(gap_pct) < threshold_pct:
        return None
    return ScanHit(
        symbol=symbol,
        scan_type=ScanType.GAP,
        score=abs(gap_pct),
        details={"prev_close": prev_close, "open": today_open, "gap_pct": gap_pct},
    )


def scan_momentum(
    symbol: str,
    bars: Sequence[Bar],
    rsi_period: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> ScanHit | None:
    """Flags a symbol whose RSI has crossed into overbought/oversold territory."""
    closes = [b.close for b in bars]
    rsi_values = rsi(closes, rsi_period)
    if not rsi_values:
        return None
    latest_rsi = rsi_values[-1]
    if latest_rsi != latest_rsi:  # still in RSI warm-up
        return None
    if latest_rsi >= overbought:
        direction = "overbought"
    elif latest_rsi <= oversold:
        direction = "oversold"
    else:
        return None
    return ScanHit(
        symbol=symbol,
        scan_type=ScanType.MOMENTUM,
        score=abs(latest_rsi - 50) / 50,
        details={"rsi": latest_rsi, "direction": direction},
    )
