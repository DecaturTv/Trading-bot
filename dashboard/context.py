import asyncio
from dataclasses import dataclass, replace

from alerts.base import Notifier
from alerts.discord_notifier import DiscordNotifier
from alerts.factory import build_alert_manager
from alerts.manager import AlertManager
from broker.alpaca_adapter import AlpacaAdapter
from broker.base import BrokerAdapter
from broker.models import Account
from config.settings import Settings
from data.bars_repository import BarsRepository
from data.database import Database
from data.ingestion import BarIngestionService
from data.schema import apply_schema
from decision_engine.scoring import WeightedFactorModel
from execution.executor import OrderExecutor
from forex.oanda_adapter import OandaAdapter
from forex.position_repository import ForexPositionRepository
from forex.position_schema import apply_forex_position_schema
from ml.feature_store_repository import FeatureStoreRepository
from ml.feature_store_schema import apply_feature_store_schema
from ml.trade_outcome_repository import TradeOutcomeRepository
from ml.trade_outcome_schema import apply_trade_outcome_schema
from risk.halt_manager import HaltManager
from risk.halt_repository import HaltRepository
from risk.halt_schema import apply_halt_schema
from risk.kelly import KellySizer
from risk.pre_trade import PreTradeChecker
from scanner.optionable_repository import OptionableSymbolsRepository
from scanner.optionable_schema import apply_optionable_schema
from scanner.service import ScannerService
from scanner.universe import UniverseManager
from scanner.universe_repository import UniverseRepository
from scanner.universe_schema import apply_universe_schema
from stocks.position_repository import StockPositionRepository
from stocks.position_schema import apply_stock_position_schema
from trade_management.models import TradeManagementConfig
from trade_management.position_state_repository import PositionStateRepository
from trade_management.position_state_schema import apply_position_state_schema


@dataclass
class AppContext:
    settings: Settings
    db: Database
    broker: BrokerAdapter
    bars_repository: BarsRepository
    ingestion_service: BarIngestionService
    universe_manager: UniverseManager
    scanner_service: ScannerService
    decision_model: WeightedFactorModel
    kelly_sizer: KellySizer
    pre_trade_checker: PreTradeChecker
    halt_manager: HaltManager
    executor: OrderExecutor
    trade_management_config: TradeManagementConfig
    position_repository: PositionStateRepository
    stock_position_repository: StockPositionRepository
    # Serializes the commit step (pre-trade check -> size -> submit order ->
    # persist position) of every equities entry -- options across all
    # timeframes (5m/15m/1h/1d) and the stock entry cycle share one Alpaca
    # account/budget, and run as independent concurrent asyncio tasks, so
    # without this two of them can both pass exposure checks against the
    # same stale snapshot and collectively overcommit capital. Read-only work
    # (bar fetch, signal scoring, option chain) stays outside the lock so
    # cycles don't serialize on the slow part, only the actual commit.
    equities_entry_lock: asyncio.Lock
    trade_outcome_repository: TradeOutcomeRepository
    feature_store_repository: FeatureStoreRepository
    alert_manager: AlertManager
    progress_notifier: Notifier | None
    forex_broker: OandaAdapter | None
    forex_position_repository: ForexPositionRepository | None


async def build_context(settings: Settings, broker: BrokerAdapter | None = None) -> AppContext:
    """broker can be injected (tests, or a future SchwabAdapter) — defaults
    to constructing AlpacaAdapter from settings."""
    db = Database.from_settings(settings)
    await db.connect()
    pool = db.pool

    await apply_schema(pool)
    await apply_universe_schema(pool)
    await apply_optionable_schema(pool)
    await apply_halt_schema(pool)
    await apply_position_state_schema(pool)
    await apply_feature_store_schema(pool)
    await apply_trade_outcome_schema(pool)
    await apply_forex_position_schema(pool)
    await apply_stock_position_schema(pool)

    broker = broker or AlpacaAdapter.from_settings(settings)

    bars_repository = BarsRepository(pool)
    ingestion_service = BarIngestionService(broker, bars_repository)

    universe_manager = UniverseManager(broker, UniverseRepository(pool), OptionableSymbolsRepository(pool))
    scanner_service = ScannerService(broker, universe_manager)

    decision_model = WeightedFactorModel()
    kelly_sizer = KellySizer(kelly_fraction=settings.kelly_fraction)

    halt_manager = HaltManager(HaltRepository(pool))
    pre_trade_checker = PreTradeChecker(halt_manager)

    executor = OrderExecutor(broker)

    trade_management_config = TradeManagementConfig(
        stop_loss_pct=settings.stop_loss_pct,
        profit_target_pct=settings.profit_target_pct,
        scale_out_fraction=settings.scale_out_fraction,
        trailing_stop_pct=settings.trailing_stop_pct,
        min_trading_days_before_expiry=settings.min_trading_days_before_expiry,
    )
    position_repository = PositionStateRepository(pool)
    stock_position_repository = StockPositionRepository(pool)
    equities_entry_lock = asyncio.Lock()
    trade_outcome_repository = TradeOutcomeRepository(pool)
    feature_store_repository = FeatureStoreRepository(pool)

    alert_manager = build_alert_manager(settings)

    progress_notifier: Notifier | None = None
    if settings.discord_webhook_url:
        progress_notifier = DiscordNotifier(settings.discord_webhook_url)

    forex_broker: OandaAdapter | None = None
    forex_position_repository: ForexPositionRepository | None = None
    if settings.oanda_api_key and settings.oanda_account_id:
        forex_broker = OandaAdapter(settings.oanda_api_key, settings.oanda_account_id, live=settings.trading_mode == "live")
        forex_position_repository = ForexPositionRepository(pool)

    return AppContext(
        settings=settings,
        db=db,
        broker=broker,
        bars_repository=bars_repository,
        ingestion_service=ingestion_service,
        universe_manager=universe_manager,
        scanner_service=scanner_service,
        decision_model=decision_model,
        kelly_sizer=kelly_sizer,
        pre_trade_checker=pre_trade_checker,
        halt_manager=halt_manager,
        executor=executor,
        trade_management_config=trade_management_config,
        position_repository=position_repository,
        stock_position_repository=stock_position_repository,
        equities_entry_lock=equities_entry_lock,
        trade_outcome_repository=trade_outcome_repository,
        feature_store_repository=feature_store_repository,
        alert_manager=alert_manager,
        progress_notifier=progress_notifier,
        forex_broker=forex_broker,
        forex_position_repository=forex_position_repository,
    )


async def close_context(context: AppContext) -> None:
    if context.forex_broker is not None:
        await context.forex_broker.aclose()
    await context.db.disconnect()


async def get_effective_account(context: AppContext) -> Account:
    """Wraps broker.get_account() — while paper trading, equity is treated as
    settings.account_start_balance rather than Alpaca's real (unrealistically
    large) paper-account equity, so position sizing, exposure/loss-limit
    checks, and the dashboard all reflect the bankroll actually being
    simulated. Live trading uses the broker's real equity unmodified."""
    account = await context.broker.get_account()
    if context.settings.trading_mode == "paper":
        return replace(account, equity=context.settings.account_start_balance)
    return account


async def get_effective_forex_account(context: AppContext) -> Account:
    """Same paper-equity override as get_effective_account, applied to the
    OANDA account instead — one simulated bankroll figure regardless of
    which broker a given cycle is trading through."""
    account = await context.forex_broker.get_account()
    if context.settings.trading_mode == "paper":
        return replace(account, equity=context.settings.account_start_balance)
    return account
