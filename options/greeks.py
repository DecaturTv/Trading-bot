import math
from dataclasses import dataclass

from broker.models import OptionRight


@dataclass(frozen=True)
class GreeksResult:
    price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 percentage point of IV
    rho: float  # per 1 percentage point of the risk-free rate


def black_scholes(
    spot: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    right: OptionRight,
    risk_free_rate: float = 0.0,
) -> GreeksResult:
    """European Black-Scholes price + Greeks — used as the standard retail
    approximation for American equity options (early exercise is rarely
    optimal absent a dividend, which we don't model here)."""
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if volatility < 0:
        raise ValueError("volatility must be >= 0")
    if time_to_expiry < 0:
        raise ValueError("time_to_expiry must be >= 0")

    if time_to_expiry == 0 or volatility == 0:
        return _boundary_result(spot, strike, right)

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry) / (
        volatility * sqrt_t
    )
    d2 = d1 - volatility * sqrt_t
    discount = math.exp(-risk_free_rate * time_to_expiry)

    if right is OptionRight.CALL:
        price = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_annual = -(spot * _norm_pdf(d1) * volatility) / (2 * sqrt_t) - risk_free_rate * strike * discount * _norm_cdf(
            d2
        )
        rho = strike * time_to_expiry * discount * _norm_cdf(d2) / 100
    else:
        price = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1
        theta_annual = -(spot * _norm_pdf(d1) * volatility) / (2 * sqrt_t) + risk_free_rate * strike * discount * _norm_cdf(
            -d2
        )
        rho = -strike * time_to_expiry * discount * _norm_cdf(-d2) / 100

    gamma = _norm_pdf(d1) / (spot * volatility * sqrt_t)
    vega = spot * _norm_pdf(d1) * sqrt_t / 100

    return GreeksResult(price=price, delta=delta, gamma=gamma, theta=theta_annual / 365, vega=vega, rho=rho)


def _boundary_result(spot: float, strike: float, right: OptionRight) -> GreeksResult:
    if right is OptionRight.CALL:
        intrinsic = max(spot - strike, 0.0)
        delta = 1.0 if spot > strike else 0.0
    else:
        intrinsic = max(strike - spot, 0.0)
        delta = -1.0 if spot < strike else 0.0
    return GreeksResult(price=intrinsic, delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
