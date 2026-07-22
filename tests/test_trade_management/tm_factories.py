from broker.models import OptionContract, OptionGreeks, OptionRight
from trade_management.models import TradeManagementConfig


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


def make_config(**overrides):
    # Matches the confirmed project rules: -50% stop, +100% scale-out, 20%
    # trailing pullback thereafter, force-close 2 trading days before expiry.
    defaults = dict(
        stop_loss_pct=0.50,
        profit_target_pct=1.00,
        scale_out_fraction=0.50,
        trailing_stop_pct=0.20,
        min_trading_days_before_expiry=2,
    )
    defaults.update(overrides)
    return TradeManagementConfig(**defaults)
