from dataclasses import dataclass
from datetime import datetime

from broker.models import OrderSide


@dataclass(frozen=True)
class OpenForexPosition:
    """A tracked FX trade — stop-loss/take-profit/trailing-stop are managed
    natively by OANDA on the order itself (attached at entry), not polled and
    evaluated locally like the options side; oanda_trade_id is what lets
    position management notice when OANDA has closed it."""

    pair: str  # e.g. "EUR_USD"
    side: OrderSide
    units: int  # always positive; side carries direction
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    oanda_trade_id: str
    opened_at: datetime
    # Links to ml_feature_snapshots for the factor breakdown behind this
    # trade's entry decision, so outcomes can be attributed back to which
    # factor drove it (see ml/feature_store_repository.py). None for
    # positions opened before this was added.
    feature_snapshot_id: int | None = None
