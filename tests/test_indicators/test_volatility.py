import random
from datetime import datetime, timedelta, timezone

import pytest
from reference import assert_close_with_nans, reference_wilder_smoothing

from broker.models import Bar
from indicators.volatility import atr


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


def reference_true_range(bars):
    trs = []
    for i, bar in enumerate(bars):
        if i == 0:
            trs.append(bar.high - bar.low)
        else:
            prev_close = bars[i - 1].close
            trs.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - prev_close),
                    abs(bar.low - prev_close),
                )
            )
    return trs


def test_atr_matches_reference_implementation():
    random.seed(3)
    closes = [100 + random.uniform(-5, 5) for _ in range(40)]
    bars = make_bars(closes, spread=2.0)

    expected = reference_wilder_smoothing(reference_true_range(bars), 14)
    assert_close_with_nans(atr(bars, 14), expected)


def test_atr_empty_input_returns_empty_list():
    assert atr([], 14) == []


def test_atr_rejects_invalid_period():
    with pytest.raises(ValueError):
        atr(make_bars([1.0, 2.0]), 0)


def test_atr_first_bar_is_high_minus_low_once_defined():
    bars = make_bars([100.0], spread=3.0)
    result = atr(bars, 1)
    assert result[0] == pytest.approx(6.0)
