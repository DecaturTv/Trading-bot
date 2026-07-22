from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from broker.models import Account, Bar, OptionRight, OrderSide
from dashboard.context import AppContext
from decision_engine.models import TradeDirection
from forex.models import OpenForexPosition
from options.models import StrategyType
from trade_management.models import OpenPositionRecord, PersistedLeg, PositionState, TradeManagementConfig


def make_bars(n=40, start_price=100.0, spread=1.0):
    now = datetime.now(timezone.utc)
    return [
        Bar(
            symbol="TEST", timestamp=now - timedelta(days=n - i), open=start_price + i,
            high=start_price + i + spread, low=start_price + i - spread, close=start_price + i, volume=1000.0,
        )
        for i in range(n)
    ]


def make_account(equity=10000.0, cash=10000.0, buying_power=10000.0):
    return Account(account_id="acct-1", equity=equity, cash=cash, buying_power=buying_power, currency="USD")


def make_position_record(symbol="AAPL", qty=2, entry_cost=500.0, scaled_out=False, peak_gain_pct=0.0, expiration=date(2026, 9, 18)):
    return OpenPositionRecord(
        symbol=symbol,
        strategy_type=StrategyType.LONG_CALL,
        direction=TradeDirection.BULLISH,
        entry_date=date(2026, 7, 1),
        legs=[
            PersistedLeg(
                symbol=f"{symbol}260918C00150000", strike=150.0, expiration=expiration,
                right=OptionRight.CALL, side=OrderSide.BUY,
            )
        ],
        state=PositionState(symbol=symbol, qty=qty, entry_cost_per_unit=entry_cost, scaled_out=scaled_out, peak_gain_pct=peak_gain_pct),
    )


def make_forex_position(
    pair="EUR_USD", side=OrderSide.BUY, units=1000, entry_price=1.1000, stop_loss_price=1.0950,
    take_profit_price=1.1100, oanda_trade_id="123",
):
    return OpenForexPosition(
        pair=pair, side=side, units=units, entry_price=entry_price, stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price, oanda_trade_id=oanda_trade_id, opened_at=datetime.now(timezone.utc),
    )


def make_context(**overrides) -> AppContext:
    """A fully-mocked AppContext — every field is an AsyncMock/MagicMock by
    default so tests only need to configure the specific calls they care
    about; overrides replaces individual fields wholesale."""
    ctx = MagicMock(spec=AppContext)
    ctx.settings = MagicMock(
        dashboard_auth_token="test-token",
        confidence_threshold=92,
        option_target_delta=0.40,
        option_target_dte=45,
        daily_loss_limit_pct=0.05,
        weekly_loss_limit_pct=0.10,
        kelly_fraction=0.25,
        forex_confidence_threshold=92,
        forex_risk_pct_per_trade=0.02,
        forex_stop_atr_multiplier=1.5,
        forex_take_profit_r_multiple=2.0,
    )
    ctx.db = AsyncMock()
    ctx.broker = AsyncMock()
    ctx.broker.get_account.return_value = make_account()
    ctx.broker.get_positions.return_value = []
    ctx.bars_repository = AsyncMock()
    ctx.ingestion_service = AsyncMock()
    ctx.universe_manager = AsyncMock()
    ctx.universe_manager.get_universe.return_value = []
    ctx.scanner_service = AsyncMock()
    ctx.decision_model = MagicMock()
    ctx.kelly_sizer = MagicMock()
    ctx.pre_trade_checker = AsyncMock()
    ctx.halt_manager = AsyncMock()
    ctx.halt_manager.is_halted.return_value = False
    ctx.executor = AsyncMock()
    ctx.trade_management_config = TradeManagementConfig(
        stop_loss_pct=0.50, profit_target_pct=1.00, scale_out_fraction=0.50,
        trailing_stop_pct=0.20, min_trading_days_before_expiry=2,
    )
    ctx.position_repository = AsyncMock()
    ctx.position_repository.get.return_value = None
    ctx.position_repository.get_all.return_value = []
    ctx.trade_outcome_repository = AsyncMock()
    ctx.trade_outcome_repository.recent_pnls.return_value = []
    ctx.trade_outcome_repository.pnls_since.return_value = []
    ctx.feature_store_repository = AsyncMock()
    ctx.alert_manager = AsyncMock()
    ctx.progress_notifier = AsyncMock()
    ctx.forex_broker = AsyncMock()
    ctx.forex_broker.get_tradeable_pairs.return_value = ["EUR_USD"]
    ctx.forex_position_repository = AsyncMock()
    ctx.forex_position_repository.get.return_value = None
    ctx.forex_position_repository.get_all.return_value = []

    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx
