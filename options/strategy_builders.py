from broker.models import OptionContract, OptionRight, OrderSide

from .models import OptionLeg, OptionStrategy, StrategyConstructionError, StrategyType

_CONTRACT_MULTIPLIER = 100


def _require_price(contract: OptionContract, field: str) -> float:
    value = getattr(contract, field)
    if value is None:
        raise StrategyConstructionError(f"{contract.symbol} is missing {field}; cannot price this leg")
    return value


def _leg_delta(leg: OptionLeg) -> float:
    if leg.contract.greeks is None or leg.contract.greeks.delta is None:
        raise StrategyConstructionError(f"{leg.contract.symbol} is missing delta")
    sign = 1 if leg.side is OrderSide.BUY else -1
    return sign * leg.contract.greeks.delta


def build_long_call(contract: OptionContract) -> OptionStrategy:
    return _build_long_option(contract, OptionRight.CALL)


def build_long_put(contract: OptionContract) -> OptionStrategy:
    return _build_long_option(contract, OptionRight.PUT)


def _build_long_option(contract: OptionContract, expected_right: OptionRight) -> OptionStrategy:
    if contract.right is not expected_right:
        raise StrategyConstructionError(
            f"expected a {expected_right.value} contract, got {contract.right.value}"
        )
    premium = _require_price(contract, "ask")
    net_debit = premium * _CONTRACT_MULTIPLIER
    leg = OptionLeg(contract=contract, side=OrderSide.BUY)

    if expected_right is OptionRight.CALL:
        max_gain = None  # theoretically unlimited upside
        strategy_type = StrategyType.LONG_CALL
    else:
        max_gain = contract.strike * _CONTRACT_MULTIPLIER - net_debit  # underlying can't go below 0
        strategy_type = StrategyType.LONG_PUT

    return OptionStrategy(
        strategy_type=strategy_type,
        legs=[leg],
        net_debit=net_debit,
        max_loss=net_debit,
        max_gain=max_gain,
        net_delta=_leg_delta(leg),
    )


def build_debit_vertical_spread(long_contract: OptionContract, short_contract: OptionContract) -> OptionStrategy:
    if long_contract.right is not short_contract.right:
        raise StrategyConstructionError("vertical spread legs must be the same option type")
    if long_contract.expiration != short_contract.expiration:
        raise StrategyConstructionError("vertical spread legs must share the same expiration")
    if long_contract.strike == short_contract.strike:
        raise StrategyConstructionError("vertical spread legs must have different strikes")

    long_leg, short_leg, net_debit = _build_debit_legs(long_contract, short_contract)
    width = abs(short_contract.strike - long_contract.strike) * _CONTRACT_MULTIPLIER

    return OptionStrategy(
        strategy_type=StrategyType.DEBIT_SPREAD_VERTICAL,
        legs=[long_leg, short_leg],
        net_debit=net_debit,
        max_loss=net_debit,
        max_gain=width - net_debit,
        net_delta=_leg_delta(long_leg) + _leg_delta(short_leg),
    )


def build_debit_diagonal_spread(long_contract: OptionContract, short_contract: OptionContract) -> OptionStrategy:
    if long_contract.right is not short_contract.right:
        raise StrategyConstructionError("diagonal spread legs must be the same option type")
    if long_contract.expiration <= short_contract.expiration:
        raise StrategyConstructionError("diagonal spread requires the long leg to expire after the short leg")

    long_leg, short_leg, net_debit = _build_debit_legs(long_contract, short_contract)

    return OptionStrategy(
        strategy_type=StrategyType.DEBIT_SPREAD_DIAGONAL,
        legs=[long_leg, short_leg],
        net_debit=net_debit,
        max_loss=net_debit,
        # The near-term expiration's payoff depends on the far leg's remaining
        # time value, which isn't a closed-form function of price the way a
        # vertical spread's width is — needs greeks.black_scholes for scenario
        # P&L, not a single static number.
        max_gain=None,
        net_delta=_leg_delta(long_leg) + _leg_delta(short_leg),
    )


def _build_debit_legs(
    long_contract: OptionContract, short_contract: OptionContract
) -> tuple[OptionLeg, OptionLeg, float]:
    long_premium = _require_price(long_contract, "ask")
    short_premium = _require_price(short_contract, "bid")
    net_debit = (long_premium - short_premium) * _CONTRACT_MULTIPLIER
    if net_debit <= 0:
        raise StrategyConstructionError(
            "this combination prices as a net credit, not a debit — disallowed by project risk rules"
        )
    return OptionLeg(contract=long_contract, side=OrderSide.BUY), OptionLeg(contract=short_contract, side=OrderSide.SELL), net_debit
