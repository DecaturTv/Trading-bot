from dataclasses import dataclass, field
from enum import Enum


class TradeDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class FactorScore:
    name: str
    value: float  # in [-1, 1]; positive = bullish, negative = bearish
    weight: float


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    direction: TradeDirection
    confidence: float  # 0-100, magnitude of conviction regardless of direction
    factors: list[FactorScore] = field(default_factory=list)
    meets_threshold: bool = False
