from dataclasses import dataclass

from alerts.factory import build_alert_manager
from alerts.manager import AlertManager
from broker.alpaca_adapter import AlpacaAdapter
from broker.base import BrokerAdapter
from config.settings import Settings
from data.bars_repository import BarsRepository
from data.database import Database
from data.ingestion import BarIngestionService
from data.schema import apply_schema
from decision_engine.scoring import WeightedFactorModel
from execution.executor import OrderExecutor
from ml.feature_store_repository import FeatureStoreRepository
from ml.feature_store_schema import apply_feature_store_schema
from ml.trade_outcome_repository import TradeOutcomeRepository
from ml.trade_outcome_schema import apply_trade_outcome_schema
from risk.halt_manager import HaltManager
from risk.halt_repository import HaltRepository
from risk.halt_schema import apply_halt_schema
from risk.kelly import KellySizer
from risk.pre_trade import PreTradeChecker
from scanner.service import ScannerService
from scanner.universe import UniverseManager
from scanner.universe_repository import UniverseRepository
from scanner.universe_schema import apply_universe_schema
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
    trade_outcome_repository: TradeOutcomeRepository
    feature_store_repository: FeatureStoreRepository
    alert_manager: AlertManager


async def build_context(settings: Settings, broker: BrokerAdapter | None = None) -> AppContext:
    """broker can be injected (tests, or a future SchwabAdapter) — defaults
    to constructing AlpacaAdapter from settings."""
    db = Database.from_settings(settings)
    await db.connect()
    pool = db.pool

    await apply_schema(pool)
    await apply_universe_schema(pool)
    await apply_halt_schema(pool)
    await apply_position_state_schema(pool)
    await apply_feature_store_schema(pool)
    await apply_trade_outcome_schema(pool)

    broker = broker or AlpacaAdapter.from_settings(settings)

    bars_repository = BarsRepository(pool)
    ingestion_service = BarIngestionService(broker, bars_repository)

    universe_manager = UniverseManager(broker, UniverseRepository(pool))
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
    trade_outcome_repository = TradeOutcomeRepository(pool)
    feature_store_repository = FeatureStoreRepository(pool)

    alert_manager = build_alert_manager(settings)

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
        trade_outcome_repository=trade_outcome_repository,
        feature_store_repository=feature_store_repository,
        alert_manager=alert_manager,
    )


async def close_context(context: AppContext) -> None:
    await context.db.disconnect()
