from datetime import datetime, timedelta, timezone

import pytest

from broker.models import Bar
from scanner.scans import scan_gap, scan_momentum, scan_unusual_volume


def make_bars(closes, volumes=None, opens=None):
    now = datetime.now(timezone.utc)
    volumes = volumes or [1000.0] * len(closes)
    opens = opens or closes
    bars = []
    for i, close in enumerate(closes):
        bars.append(
            Bar(
                symbol="TEST",
                timestamp=now + timedelta(days=i),
                open=opens[i],
                high=close + 1,
                low=close - 1,
                close=close,
                volume=volumes[i],
            )
        )
    return bars


def test_scan_unusual_volume_flags_spike():
    closes = [100.0] * 21
    volumes = [1000.0] * 20 + [5000.0]
    bars = make_bars(closes, volumes=volumes)

    hit = scan_unusual_volume("TEST", bars, lookback=20, threshold=2.0)

    assert hit is not None
    assert hit.details["ratio"] == pytest.approx(5.0)


def test_scan_unusual_volume_no_hit_when_normal():
    bars = make_bars([100.0] * 21, volumes=[1000.0] * 21)
    assert scan_unusual_volume("TEST", bars, lookback=20, threshold=2.0) is None


def test_scan_unusual_volume_insufficient_history():
    assert scan_unusual_volume("TEST", make_bars([100.0] * 5), lookback=20) is None


def test_scan_gap_flags_gap_up():
    bars = make_bars([100.0, 100.0], opens=[100.0, 110.0])
    hit = scan_gap("TEST", bars, threshold_pct=0.03)

    assert hit is not None
    assert hit.details["gap_pct"] == pytest.approx(0.10)


def test_scan_gap_no_hit_when_small():
    bars = make_bars([100.0, 100.0], opens=[100.0, 101.0])
    assert scan_gap("TEST", bars, threshold_pct=0.03) is None


def test_scan_gap_insufficient_history():
    assert scan_gap("TEST", make_bars([100.0])) is None


def test_scan_momentum_flags_overbought():
    closes = [float(i) for i in range(1, 30)]  # strictly increasing -> RSI 100
    hit = scan_momentum("TEST", make_bars(closes), rsi_period=14, overbought=70.0)

    assert hit is not None
    assert hit.details["direction"] == "overbought"


def test_scan_momentum_flags_oversold():
    closes = [float(30 - i) for i in range(29)]  # strictly decreasing -> RSI 0
    hit = scan_momentum("TEST", make_bars(closes), rsi_period=14, oversold=30.0)

    assert hit is not None
    assert hit.details["direction"] == "oversold"


def test_scan_momentum_no_hit_when_neutral():
    bars = make_bars([100.0] * 30)
    assert scan_momentum("TEST", bars, rsi_period=14) is None
