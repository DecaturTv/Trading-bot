import random

import pytest
from reference import assert_close_with_nans, reference_ema, reference_rsi

from indicators.momentum import macd, rsi


def test_rsi_matches_reference_implementation():
    random.seed(7)
    values = [100 + random.uniform(-5, 5) for _ in range(60)]
    assert_close_with_nans(rsi(values, 14), reference_rsi(values, 14))


def test_rsi_is_100_when_strictly_increasing():
    values = [float(i) for i in range(1, 30)]
    result = rsi(values, 14)
    assert result[-1] == pytest.approx(100.0)


def test_rsi_is_neutral_when_flat():
    values = [100.0] * 30
    result = rsi(values, 14)
    assert result[-1] == pytest.approx(50.0)


def test_rsi_rejects_invalid_period():
    with pytest.raises(ValueError):
        rsi([1.0, 2.0], 0)


def test_macd_matches_reference_implementation():
    random.seed(11)
    values = [100 + random.uniform(-5, 5) for _ in range(80)]
    result = macd(values, fast=12, slow=26, signal=9)

    expected_macd_line = [
        a - b for a, b in zip(reference_ema(values, 12), reference_ema(values, 26))
    ]
    assert_close_with_nans(result.macd_line, expected_macd_line)

    expected_signal_line = reference_ema(expected_macd_line, 9)
    assert_close_with_nans(result.signal_line, expected_signal_line)

    expected_histogram = [a - b for a, b in zip(expected_macd_line, expected_signal_line)]
    assert_close_with_nans(result.histogram, expected_histogram)


def test_macd_rejects_fast_not_less_than_slow():
    with pytest.raises(ValueError):
        macd([1.0, 2.0, 3.0], fast=26, slow=12)
