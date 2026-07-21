from dataclasses import dataclass


@dataclass(frozen=True)
class MACDResult:
    macd_line: list[float]
    signal_line: list[float]
    histogram: list[float]


@dataclass(frozen=True)
class SuperTrendResult:
    trend: list[float]
    direction: list[int]  # 1 = uptrend, -1 = downtrend, 0 = undefined (warm-up)
