from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeatureSnapshot:
    id: int | None
    symbol: str
    as_of: datetime
    factors: dict[str, float]
    confidence: float
    direction: str
    pnl: float | None = None
    win: bool | None = None


@dataclass(frozen=True)
class TrainingResult:
    model: object  # a fitted xgboost.XGBClassifier
    feature_names: list[str]
    accuracy: float
    auc: float | None  # None when the test split has only one class present
    train_size: int
    test_size: int
