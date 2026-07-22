import pytest

from broker.models import OptionRight
from options.greeks import black_scholes
from options.implied_volatility import implied_volatility


@pytest.mark.parametrize("right", [OptionRight.CALL, OptionRight.PUT])
@pytest.mark.parametrize("strike", [80.0, 100.0, 120.0])
@pytest.mark.parametrize("true_sigma", [0.15, 0.35, 0.6])
def test_round_trip_recovers_original_volatility(right, strike, true_sigma):
    spot, t, r = 100.0, 0.5, 0.03
    market_price = black_scholes(spot, strike, t, true_sigma, right, r).price

    recovered = implied_volatility(market_price, spot, strike, t, right, r)

    assert recovered == pytest.approx(true_sigma, abs=1e-4)


def test_rejects_non_positive_market_price():
    with pytest.raises(ValueError):
        implied_volatility(0.0, 100.0, 100.0, 0.5, OptionRight.CALL)
    with pytest.raises(ValueError):
        implied_volatility(-1.0, 100.0, 100.0, 0.5, OptionRight.CALL)


def test_rejects_non_positive_time_to_expiry():
    with pytest.raises(ValueError):
        implied_volatility(5.0, 100.0, 100.0, 0.0, OptionRight.CALL)


def test_rejects_price_below_intrinsic():
    with pytest.raises(ValueError):
        implied_volatility(market_price=1.0, spot=120.0, strike=100.0, time_to_expiry=0.5, right=OptionRight.CALL)
