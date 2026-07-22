from dataclasses import dataclass, field
from enum import Enum


class ScanType(str, Enum):
    UNUSUAL_VOLUME = "unusual_volume"
    GAP = "gap"
    MOMENTUM = "momentum"


@dataclass(frozen=True)
class ScanHit:
    symbol: str
    scan_type: ScanType
    score: float  # normalized signal strength; higher = stronger. Not a trade confidence score — decision_engine/ owns that.
    details: dict = field(default_factory=dict)
