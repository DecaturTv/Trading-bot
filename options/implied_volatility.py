import math

from broker.models import OptionRight

from .greeks import black_scholes


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    right: OptionRight,
    risk_free_rate: float = 0.0,
    initial_guess: float = 0.3,
    tolerance: float = 1e-6,
    max_iterations: int = 50,
) -> float:
    """Solves for the volatility that reproduces market_price under Black-Scholes.

    Newton-Raphson (fast, using vega as the derivative) with a bisection
    fallback for cases where vega is too small to converge (deep ITM/OTM or
    near expiry).
    """
    if market_price <= 0:
        raise ValueError("market_price must be positive")
    if time_to_expiry <= 0:
        raise ValueError("time_to_expiry must be positive")

    # No-arbitrage floor for a European option, discounting the strike by the
    # time value of money — NOT the naive spot/strike intrinsic value, which
    # can sit above this floor (e.g. a deep ITM put under a positive rate).
    discounted_strike = strike * math.exp(-risk_free_rate * time_to_expiry)
    floor = max(spot - discounted_strike, 0.0) if right is OptionRight.CALL else max(discounted_strike - spot, 0.0)
    if market_price < floor - tolerance:
        raise ValueError("market_price is below the no-arbitrage floor; cannot solve for a valid volatility")

    sigma = initial_guess
    for _ in range(max_iterations):
        result = black_scholes(spot, strike, time_to_expiry, sigma, right, risk_free_rate)
        diff = result.price - market_price
        if abs(diff) < tolerance:
            return sigma
        vega_raw = result.vega * 100  # undo the "per 1 point" scaling used for reporting
        if vega_raw < 1e-8:
            break
        sigma = max(sigma - diff / vega_raw, 1e-6)

    return _bisection_iv(market_price, spot, strike, time_to_expiry, right, risk_free_rate, tolerance, max_iterations)


def _bisection_iv(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    right: OptionRight,
    risk_free_rate: float,
    tolerance: float,
    max_iterations: int,
) -> float:
    low, high = 1e-6, 5.0
    for _ in range(max_iterations):
        mid = (low + high) / 2
        price = black_scholes(spot, strike, time_to_expiry, mid, right, risk_free_rate).price
        if abs(price - market_price) < tolerance:
            return mid
        if price > market_price:
            high = mid
        else:
            low = mid
    return (low + high) / 2
