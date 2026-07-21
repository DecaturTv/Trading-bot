from datetime import datetime, timedelta, timezone

import pytest

from broker.models import Bar
from indicators.trend import supertrend


def make_bars(closes, spread=1.0):
    now = datetime.now(timezone.utc)
    bars = []
    for i, close in enumerate(closes):
        bars.append(
            Bar(
                symbol="TEST",
                timestamp=now + timedelta(minutes=i),
                open=close,
                high=close + spread,
                low=close - spread,
                close=close,
                volume=1000,
            )
        )
    return bars


def test_supertrend_direction_values_are_valid():
    closes = [100 + i * 0.5 for i in range(40)]
    bars = make_bars(closes)
    result = supertrend(bars, period=10, multiplier=3.0)
    assert set(result.direction) <= {-1, 0, 1}


def test_supertrend_nan_during_warmup():
    closes = [100 + i * 0.5 for i in range(40)]
    bars = make_bars(closes)
    result = supertrend(bars, period=10, multiplier=3.0)
    for i in range(9):
        assert result.trend[i] != result.trend[i]
        assert result.direction[i] == 0
    assert result.trend[9] == result.trend[9]


def test_supertrend_identifies_uptrend_on_steadily_rising_prices():
    closes = [100 + i * 1.5 for i in range(60)]
    bars = make_bars(closes, spread=0.5)
    result = supertrend(bars, period=10, multiplier=3.0)

    tail_directions = result.direction[-10:]
    assert all(d == 1 for d in tail_directions)
    for i in range(50, 60):
        assert result.trend[i] < bars[i].close


def test_supertrend_identifies_downtrend_on_steadily_falling_prices():
    closes = [200 - i * 1.5 for i in range(60)]
    bars = make_bars(closes, spread=0.5)
    result = supertrend(bars, period=10, multiplier=3.0)

    tail_directions = result.direction[-10:]
    assert all(d == -1 for d in tail_directions)
    for i in range(50, 60):
        assert result.trend[i] > bars[i].close


def test_supertrend_rejects_invalid_period():
    with pytest.raises(ValueError):
        supertrend(make_bars([1.0, 2.0]), period=0)


def test_supertrend_empty_input():
    result = supertrend([], period=10)
    assert result.trend == []
    assert result.direction == []
