from broker.models import OptionContract, OrderSide
from options.models import OptionStrategy


def unrealized_gain_pct(entry_cost_per_unit: float, current_value_per_unit: float) -> float:
    if entry_cost_per_unit <= 0:
        raise ValueError("entry_cost_per_unit must be positive")
    return (current_value_per_unit - entry_cost_per_unit) / entry_cost_per_unit


def current_value_per_unit(strategy: OptionStrategy, current_contracts: dict[str, OptionContract]) -> float:
    """Mark-to-market value of one unit of the strategy at current mid prices —
    what closing the position right now would be worth per unit, in dollars
    (100x multiplier, same scale as OptionStrategy.net_debit)."""
    total = 0.0
    for leg in strategy.legs:
        contract = current_contracts.get(leg.contract.symbol)
        if contract is None or contract.bid is None or contract.ask is None:
            raise ValueError(f"missing current bid/ask for {leg.contract.symbol}")
        mid = (contract.bid + contract.ask) / 2
        total += mid if leg.side is OrderSide.BUY else -mid
    return total * 100
