from datetime import datetime, timedelta, timezone

import pytest
from bar_factories import make_bars

from broker.models import Bar
from decision_engine.factors import (
    candlestick_factor,
    gap_factor,
    macd_factor,
    momentum_factor,
    trend_factor,
    unusual_volume_factor,
)
from scanner.models import ScanHit, ScanType


def make_bar(open, high, low, close, i=0):
    return Bar(
        symbol="TEST", timestamp=datetime.now(timezone.utc) + timedelta(minutes=i),
        open=open, high=high, low=low, close=close, volume=1000.0,
    )


def test_momentum_factor_bullish_when_strictly_increasing():
    closes = [float(i) for i in range(1, 30)]
    value = momentum_factor(make_bars(closes))
    assert value == pytest.approx(1.0)


def test_momentum_factor_bearish_when_strictly_decreasing():
    closes = [float(30 - i) for i in range(29)]
    value = momentum_factor(make_bars(closes))
    assert value == pytest.approx(-1.0)


def test_momentum_factor_neutral_when_flat():
    value = momentum_factor(make_bars([100.0] * 30))
    assert value == pytest.approx(0.0)


def test_momentum_factor_none_during_warmup():
    assert momentum_factor(make_bars([100.0] * 5), period=14) is None


def test_macd_factor_positive_on_sustained_uptrend():
    closes = [100 + i * 0.5 for i in range(60)]
    value = macd_factor(make_bars(closes))
    assert value is not None
    assert value > 0


def test_macd_factor_negative_on_sustained_downtrend():
    closes = [200 - i * 0.5 for i in range(60)]
    value = macd_factor(make_bars(closes))
    assert value is not None
    assert value < 0


def test_macd_factor_none_during_warmup():
    assert macd_factor(make_bars([100.0] * 10)) is None


def test_trend_factor_bullish_on_uptrend():
    closes = [100 + i * 1.5 for i in range(60)]
    value = trend_factor(make_bars(closes, spread=0.5))
    assert value is not None
    assert value > 0


def test_trend_factor_bearish_on_downtrend():
    closes = [200 - i * 1.5 for i in range(60)]
    value = trend_factor(make_bars(closes, spread=0.5))
    assert value is not None
    assert value < 0


def test_trend_factor_none_during_warmup():
    assert trend_factor(make_bars([100.0] * 5), period=10) is None


def test_unusual_volume_factor_bullish_on_up_day():
    bars = make_bars([100.0, 105.0])
    hit = ScanHit(symbol="TEST", scan_type=ScanType.UNUSUAL_VOLUME, score=3.0, details={})
    value = unusual_volume_factor(bars, [hit])
    assert value is not None
    assert value > 0


def test_unusual_volume_factor_bearish_on_down_day():
    bars = make_bars([105.0, 100.0])
    hit = ScanHit(symbol="TEST", scan_type=ScanType.UNUSUAL_VOLUME, score=3.0, details={})
    value = unusual_volume_factor(bars, [hit])
    assert value is not None
    assert value < 0


def test_unusual_volume_factor_none_without_hit():
    assert unusual_volume_factor(make_bars([100.0, 105.0]), []) is None


def test_gap_factor_scales_with_gap_pct():
    hit = ScanHit(symbol="TEST", scan_type=ScanType.GAP, score=0.05, details={"gap_pct": 0.05})
    assert gap_factor([hit]) == pytest.approx(0.5)


def test_gap_factor_clips_beyond_full_scale():
    hit = ScanHit(symbol="TEST", scan_type=ScanType.GAP, score=0.20, details={"gap_pct": -0.20})
    assert gap_factor([hit]) == -1.0


def test_gap_factor_none_without_hit():
    assert gap_factor([]) is None


def test_candlestick_factor_positive_on_bullish_pattern():
    prev = make_bar(open=110, high=111, low=99, close=100, i=0)
    curr = make_bar(open=99, high=114, low=98, close=113, i=1)
    value = candlestick_factor([prev, curr])
    assert value is not None
    assert value > 0


def test_candlestick_factor_negative_on_bearish_pattern():
    prev = make_bar(open=100, high=111, low=99, close=110, i=0)
    curr = make_bar(open=111, high=112, low=96, close=97, i=1)
    value = candlestick_factor([prev, curr])
    assert value is not None
    assert value < 0


def test_candlestick_factor_none_without_a_pattern():
    bars = [make_bar(open=50, high=50.2, low=49.8, close=50.05, i=i) for i in range(10)]
    assert candlestick_factor(bars) is None
