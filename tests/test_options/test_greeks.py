import math

import pytest

from broker.models import OptionRight
from options.greeks import black_scholes


def test_put_call_parity():
    spot, strike, t, sigma, r = 100.0, 105.0, 0.5, 0.25, 0.03
    call = black_scholes(spot, strike, t, sigma, OptionRight.CALL, r)
    put = black_scholes(spot, strike, t, sigma, OptionRight.PUT, r)

    lhs = call.price - put.price
    rhs = spot - strike * math.exp(-r * t)
    assert lhs == pytest.approx(rhs, abs=1e-9)


@pytest.mark.parametrize("right", [OptionRight.CALL, OptionRight.PUT])
def test_delta_matches_finite_difference(right):
    spot, strike, t, sigma, r = 100.0, 105.0, 0.5, 0.25, 0.03
    h = 1e-4
    base = black_scholes(spot, strike, t, sigma, right, r)
    up = black_scholes(spot + h, strike, t, sigma, right, r)
    down = black_scholes(spot - h, strike, t, sigma, right, r)
    fd_delta = (up.price - down.price) / (2 * h)
    assert base.delta == pytest.approx(fd_delta, abs=1e-4)


@pytest.mark.parametrize("right", [OptionRight.CALL, OptionRight.PUT])
def test_gamma_matches_finite_difference(right):
    spot, strike, t, sigma, r = 100.0, 105.0, 0.5, 0.25, 0.03
    h = 1e-3
    base = black_scholes(spot, strike, t, sigma, right, r)
    up = black_scholes(spot + h, strike, t, sigma, right, r)
    down = black_scholes(spot - h, strike, t, sigma, right, r)
    fd_gamma = (up.price - 2 * base.price + down.price) / (h**2)
    assert base.gamma == pytest.approx(fd_gamma, abs=1e-3)


@pytest.mark.parametrize("right", [OptionRight.CALL, OptionRight.PUT])
def test_vega_matches_finite_difference(right):
    spot, strike, t, sigma, r = 100.0, 105.0, 0.5, 0.25, 0.03
    h = 1e-4
    base = black_scholes(spot, strike, t, sigma, right, r)
    up = black_scholes(spot, strike, t, sigma + h, right, r)
    down = black_scholes(spot, strike, t, sigma - h, right, r)
    fd_vega_raw = (up.price - down.price) / (2 * h)
    assert base.vega * 100 == pytest.approx(fd_vega_raw, abs=1e-3)


@pytest.mark.parametrize("right", [OptionRight.CALL, OptionRight.PUT])
def test_theta_matches_finite_difference(right):
    spot, strike, t, sigma, r = 100.0, 105.0, 0.5, 0.25, 0.03
    h = 1e-4
    base = black_scholes(spot, strike, t, sigma, right, r)
    up = black_scholes(spot, strike, t + h, sigma, right, r)
    down = black_scholes(spot, strike, t - h, sigma, right, r)
    fd_dprice_dT = (up.price - down.price) / (2 * h)
    # theta is reported as decay per day; convert back to the annual rate
    # (dPrice/dT) it was derived from before comparing to the finite difference.
    assert base.theta * 365 == pytest.approx(-fd_dprice_dT, abs=1e-3)


@pytest.mark.parametrize("right", [OptionRight.CALL, OptionRight.PUT])
def test_rho_matches_finite_difference(right):
    spot, strike, t, sigma, r = 100.0, 105.0, 0.5, 0.25, 0.03
    h = 1e-4
    base = black_scholes(spot, strike, t, sigma, right, r)
    up = black_scholes(spot, strike, t, sigma, right, r + h)
    down = black_scholes(spot, strike, t, sigma, right, r - h)
    fd_rho_raw = (up.price - down.price) / (2 * h)
    assert base.rho * 100 == pytest.approx(fd_rho_raw, abs=1e-3)


def test_zero_time_to_expiry_returns_intrinsic_value():
    itm_call = black_scholes(110.0, 100.0, 0.0, 0.25, OptionRight.CALL)
    assert itm_call.price == pytest.approx(10.0)
    assert itm_call.delta == 1.0
    assert itm_call.gamma == 0.0

    otm_call = black_scholes(90.0, 100.0, 0.0, 0.25, OptionRight.CALL)
    assert otm_call.price == pytest.approx(0.0)
    assert otm_call.delta == 0.0


def test_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        black_scholes(-1.0, 100.0, 0.5, 0.25, OptionRight.CALL)
    with pytest.raises(ValueError):
        black_scholes(100.0, -1.0, 0.5, 0.25, OptionRight.CALL)
    with pytest.raises(ValueError):
        black_scholes(100.0, 100.0, 0.5, -0.1, OptionRight.CALL)
    with pytest.raises(ValueError):
        black_scholes(100.0, 100.0, -0.1, 0.25, OptionRight.CALL)
