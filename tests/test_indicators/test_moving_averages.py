import random

import pytest
from reference import assert_close_with_nans, reference_ema

from indicators.moving_averages import ema, sma


def test_ema_matches_reference_implementation():
    random.seed(42)
    values = [100 + random.uniform(-5, 5) for _ in range(50)]
    assert_close_with_nans(ema(values, 10), reference_ema(values, 10))


def test_ema_period_one_equals_input():
    values = [1.0, 2.0, 3.0]
    assert ema(values, 1) == values


def test_ema_rejects_invalid_period():
    with pytest.raises(ValueError):
        ema([1.0, 2.0], 0)


def test_ema_nan_during_warmup():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = ema(values, 3)
    assert result[0] != result[0]
    assert result[1] != result[1]
    assert result[2] == result[2]


def test_sma_basic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = sma(values, 3)
    assert result[0] != result[0]
    assert result[1] != result[1]
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


def test_sma_rejects_invalid_period():
    with pytest.raises(ValueError):
        sma([1.0], 0)
