from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from broker.models import OptionRight, OrderSide
from options.greeks import black_scholes


@dataclass(frozen=True)
class SimulatedLeg:
    strike: float
    expiration: date
    right: OptionRight
    side: OrderSide


def simulated_strategy_value(
    legs: Sequence[SimulatedLeg],
    underlying_price: float,
    as_of: date,
    volatility: float,
    risk_free_rate: float = 0.0,
) -> float:
    """Theoretical value of one unit of the strategy at as_of, priced via
    Black-Scholes against underlying_price/volatility. This project has no
    historical options market data (bid/ask/IV) to backtest against, so this
    is a model-based approximation, not a replay of real historical quotes.
    """
    total = 0.0
    for leg in legs:
        time_to_expiry = max((leg.expiration - as_of).days, 0) / 365
        price = black_scholes(underlying_price, leg.strike, time_to_expiry, volatility, leg.right, risk_free_rate).price
        total += price if leg.side is OrderSide.BUY else -price
    return total * 100


def build_synthetic_chain(
    underlying_price: float,
    expiration: date,
    as_of: date,
    volatility: float,
    right: OptionRight,
    strike_increment: float,
    num_strikes: int = 20,
    risk_free_rate: float = 0.0,
) -> list[tuple[float, float]]:
    """Synthetic (strike, delta) pairs around underlying_price, computed via
    Black-Scholes — a stand-in for a real historical chain, which this
    project doesn't have.
    """
    if strike_increment <= 0:
        raise ValueError("strike_increment must be positive")
    time_to_expiry = max((expiration - as_of).days, 0) / 365
    chain = []
    for i in range(-num_strikes, num_strikes + 1):
        strike = round((underlying_price + i * strike_increment) / strike_increment) * strike_increment
        if strike <= 0:
            continue
        greeks = black_scholes(underlying_price, strike, time_to_expiry, volatility, right, risk_free_rate)
        chain.append((strike, greeks.delta))
    return chain


def select_synthetic_strike_by_delta(chain: Sequence[tuple[float, float]], target_delta: float) -> float:
    if not chain:
        raise ValueError("chain must not be empty")
    return min(chain, key=lambda pair: abs(abs(pair[1]) - abs(target_delta)))[0]
