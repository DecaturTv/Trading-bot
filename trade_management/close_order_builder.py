import math

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
    price_step: float = 1.0,
) -> OrderRequest | MultiLegOrderRequest:
    """Builds the broker order to close an existing position opened via strategy.

    Each leg's side is reversed from how it was opened (sell what was bought,
    buy back what was sold) and tagged *_TO_CLOSE. Pricing uses current_contracts
    (fresh quotes), not strategy's original leg contracts — those were captured
    at open time and may only have ask populated (build_long_call only
    validates ask, never bid), which is the wrong side to price a close against.

    price_step (single-leg only) sets how far from the mid toward the far
    side of the quote the limit goes: 0.0 = mid, 1.0 = the bid for a sell
    (the ask for a buy), which is the default and the old behavior. See
    trade_management/close_execution.py for the walk that steps through it.
    """
    if qty <= 0:
        raise ValueError("qty must be positive")

    if len(strategy.legs) == 1:
        leg = strategy.legs[0]
        close_side = _OPPOSITE_SIDE[leg.side]
        contract = _current_contract(current_contracts, leg.contract.symbol)
        limit_price = _walked_price(contract, close_side, price_step)
        return OrderRequest(
            symbol=leg.contract.symbol,
            qty=qty,
            side=close_side,
            order_type=OrderType.LIMIT,
            time_in_force=time_in_force,
            limit_price=limit_price,
            # Without this tag Alpaca reads a SELL on a long option as opening
            # a naked short and rejects it as "uncovered" — the close never
            # fills and the position rots to its max-hold force-exit.
            position_intent=(
                PositionIntent.SELL_TO_CLOSE if close_side is OrderSide.SELL else PositionIntent.BUY_TO_CLOSE
            ),
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


def _walked_price(contract: OptionContract, close_side: OrderSide, price_step: float) -> float:
    far = _closing_price(contract, close_side)
    if price_step >= 1.0 or contract.bid is None or contract.ask is None or contract.ask <= contract.bid:
        return far
    mid = (contract.bid + contract.ask) / 2
    raw = mid + (far - mid) * max(price_step, 0.0)
    tick = _tick_size(contract, raw)
    if close_side is OrderSide.SELL:
        # round down (toward a fill), never below the bid
        return max(far, round(math.floor(round(raw / tick, 6)) * tick, 2))
    return min(far, round(math.ceil(round(raw / tick, 6)) * tick, 2))


def _tick_size(contract: OptionContract, price: float) -> float:
    """Penny increments if the quote itself is in pennies (penny-pilot
    names), otherwise the standard $0.05 below $3 / $0.10 at or above."""
    if any(abs(p * 20 - round(p * 20)) > 1e-6 for p in (contract.bid, contract.ask)):
        return 0.01
    return 0.05 if price < 3.0 else 0.10
