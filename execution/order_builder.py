from broker.models import (
    MultiLegOrderLeg,
    MultiLegOrderRequest,
    OrderRequest,
    OrderSide,
    OrderType,
    PositionIntent,
    TimeInForce,
)
from options.models import OptionStrategy


def build_open_order_request(
    strategy: OptionStrategy, qty: int, time_in_force: TimeInForce = TimeInForce.DAY
) -> OrderRequest | MultiLegOrderRequest:
    """Builds the broker order to open strategy as a new position.

    Single-leg strategies (long call/put) submit as a normal limit order.
    Multi-leg strategies (debit spreads) submit as one atomic combo order —
    see broker/'s MultiLegOrderRequest — so a partial fill can't leave one
    leg open without its hedge.
    """
    if qty <= 0:
        raise ValueError("qty must be positive")

    if len(strategy.legs) == 1:
        leg = strategy.legs[0]
        # Strategy construction (options/strategy_builders.py) already
        # guarantees bid/ask are populated for every leg.
        limit_price = leg.contract.ask if leg.side is OrderSide.BUY else leg.contract.bid
        return OrderRequest(
            symbol=leg.contract.symbol,
            qty=qty,
            side=leg.side,
            order_type=OrderType.LIMIT,
            time_in_force=time_in_force,
            limit_price=limit_price,
        )

    return MultiLegOrderRequest(
        legs=[
            MultiLegOrderLeg(
                symbol=leg.contract.symbol,
                side=leg.side,
                position_intent=PositionIntent.BUY_TO_OPEN if leg.side is OrderSide.BUY else PositionIntent.SELL_TO_OPEN,
            )
            for leg in strategy.legs
        ],
        qty=qty,
        # net_debit is in dollars with the 100x contract multiplier applied
        # (see options/models.py); undo that to get the per-contract limit
        # price the broker API expects.
        limit_price=strategy.net_debit / 100,
        time_in_force=time_in_force,
    )
