import math

import pytest
from bt_factories import make_bars

from backtesting.volatility_estimator import realized_volatility


def test_returns_none_when_insufficient_history():
    bars = make_bars([100.0] * 5)
    assert realized_volatility(bars, lookback=20) is None


def test_zero_volatility_for_flat_prices():
    bars = make_bars([100.0] * 30)
    vol = realized_volatility(bars, lookback=20)
    assert vol == pytest.approx(0.0, abs=1e-9)


def test_matches_hand_calculation_for_alternating_returns():
    # Alternate +1%/-1% (approx) log returns for a known, hand-computable variance.
    closes = [100.0]
    for i in range(20):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    bars = make_bars(closes)

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    expected = math.sqrt(variance) * math.sqrt(252)

    vol = realized_volatility(bars, lookback=20)
    assert vol == pytest.approx(expected)


def test_rejects_invalid_lookback():
    with pytest.raises(ValueError):
        realized_volatility(make_bars([100.0] * 5), lookback=0)
