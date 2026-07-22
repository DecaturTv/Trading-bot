from broker.models import (
    MultiLegOrderLeg,
    MultiLegOrderRequest,
    OptionContract,
    OrderRequest,
    OrderSide,
    OrderType,
    PositionIntent,
    TimeInForce,
)
from options.models import OptionStrategy

_OPPOSITE_SIDE = {OrderSide.BUY: OrderSide.SELL, OrderSide.SELL: OrderSide.BUY}


def build_close_order_request(
    strategy: OptionStrategy,
    qty: int,
    current_contracts: dict[str, OptionContract],
    time_in_force: TimeInForce = TimeInForce.DAY,
) -> OrderRequest | MultiLegOrderRequest:
    """Builds the broker order to close an existing position opened via strategy.

    Each leg's side is reversed from how it was opened (sell what was bought,
    buy back what was sold) and tagged *_TO_CLOSE. Pricing uses current_contracts
    (fresh quotes), not strategy's original leg contracts — those were captured
    at open time and may only have ask populated (build_long_call only
    validates ask, never bid), which is the wrong side to price a close against.
    """
    if qty <= 0:
        raise ValueError("qty must be positive")

    if len(strategy.legs) == 1:
        leg = strategy.legs[0]
        close_side = _OPPOSITE_SIDE[leg.side]
        contract = _current_contract(current_contracts, leg.contract.symbol)
        limit_price = _closing_price(contract, close_side)
        return OrderRequest(
            symbol=leg.contract.symbol,
            qty=qty,
            side=close_side,
            order_type=OrderType.LIMIT,
            time_in_force=time_in_force,
            limit_price=limit_price,
        )

    legs = []
    net_close_credit = 0.0
    for leg in strategy.legs:
        close_side = _OPPOSITE_SIDE[leg.side]
        contract = _current_contract(current_contracts, leg.contract.symbol)
        price = _closing_price(contract, close_side)
        net_close_credit += price if close_side is OrderSide.SELL else -price
        legs.append(
            MultiLegOrderLeg(
                symbol=leg.contract.symbol,
                side=close_side,
                position_intent=(
                    PositionIntent.SELL_TO_CLOSE if close_side is OrderSide.SELL else PositionIntent.BUY_TO_CLOSE
                ),
            )
        )

    return MultiLegOrderRequest(
        legs=legs,
        qty=qty,
        # BEST-EFFORT, UNVERIFIED SIGN CONVENTION: opening a debit spread uses
        # a positive limit_price (what you pay). Closing a profitable debit
        # spread nets a credit, so this negates it on the assumption Alpaca
        # follows the same debit-positive/credit-negative convention for
        # MLEG orders. This has not been checked against a live/paper Alpaca
        # account (no credentials in this environment) — verify against a
        # real paper order before trusting this for anything but a dry run.
        limit_price=-net_close_credit,
        time_in_force=time_in_force,
    )


def _current_contract(current_contracts: dict[str, OptionContract], symbol: str) -> OptionContract:
    contract = current_contracts.get(symbol)
    if contract is None:
        raise ValueError(f"missing current quote for {symbol}")
    return contract


def _closing_price(contract: OptionContract, close_side: OrderSide) -> float:
    price = contract.bid if close_side is OrderSide.SELL else contract.ask
    if price is None:
        side_name = "bid" if close_side is OrderSide.SELL else "ask"
        raise ValueError(f"missing current {side_name} for {contract.symbol}")
    return price
