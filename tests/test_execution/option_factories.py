from broker.models import OptionContract, OptionGreeks, OptionRight


def make_contract(
    symbol,
    strike,
    expiration,
    right=OptionRight.CALL,
    bid=None,
    ask=None,
    last_price=None,
    implied_volatility=None,
    delta=None,
    underlying_symbol="AAPL",
):
    greeks = OptionGreeks(delta=delta, gamma=0.02, theta=-0.05, vega=0.1, rho=0.01) if delta is not None else None
    return OptionContract(
        symbol=symbol,
        underlying_symbol=underlying_symbol,
        strike=strike,
        expiration=expiration,
        right=right,
        bid=bid,
        ask=ask,
        last_price=last_price,
        implied_volatility=implied_volatility,
        greeks=greeks,
    )
