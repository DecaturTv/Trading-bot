from collections.abc import Sequence
from dataclasses import dataclass

from broker.models import Bar
from indicators.volatility import atr

from .models import TradeDirection


@dataclass(frozen=True)
class SRLevelConfig:
    """Support/resistance detection + entry parameters.

    No defaults on purpose, same "confirmed business decision" convention as
    TradeManagementConfig — these are the exact numbers walk-forward backtested
    in _scratch_sr_backtest.py against 2.5 months of real 5Min bars (165
    symbols, 18,221 trades): +0.21R to +0.29R expectancy, positive in every one
    of the 9 weeks in the sample, holds under realistic concurrent-position
    caps (3/5/10). See project memory on the S/R backtest.
    """

    pivot_k: int  # bars required on each side to confirm a swing point
    level_lookback_bars: int  # trailing window swings are drawn from
    cluster_tol: float  # group swing prices within this fraction into one level
    min_touches: int  # a level needs this many clustered swings to count
    touch_tol: float  # price must come within this fraction of a level to react to it
    atr_period: int
    stop_atr_mult: float  # stop = level +/- this many ATRs
    min_reward_r: float  # skip the trade if the nearest level in-direction doesn't clear this many R
    fallback_target_r: float  # target when no level exists in the trade's direction (R multiple of risk)

    def __post_init__(self):
        if self.pivot_k < 1:
            raise ValueError("pivot_k must be >= 1")
        if self.level_lookback_bars < 1:
            raise ValueError("level_lookback_bars must be >= 1")
        if self.cluster_tol <= 0:
            raise ValueError("cluster_tol must be positive")
        if self.min_touches < 1:
            raise ValueError("min_touches must be >= 1")
        if self.touch_tol <= 0:
            raise ValueError("touch_tol must be positive")
        if self.atr_period < 1:
            raise ValueError("atr_period must be >= 1")
        if self.stop_atr_mult <= 0:
            raise ValueError("stop_atr_mult must be positive")
        if self.min_reward_r <= 0:
            raise ValueError("min_reward_r must be positive")
        if self.fallback_target_r <= 0:
            raise ValueError("fallback_target_r must be positive")

    @property
    def min_bars_required(self) -> int:
        return self.level_lookback_bars + 2 * self.pivot_k + self.atr_period + 2


# The exact parameters backtested in _scratch_sr_backtest.py — not tuned
# further here. 5Min bars: pivot_k=5 (25 min either side to confirm a swing),
# level_lookback_bars=780 (~10 trading days), cluster_tol/touch_tol in
# fractional price terms, stop 0.5 ATR beyond the level, skip unless the
# nearest opposing level clears 1.5R, fallback target 2R with no level.
BACKTESTED_SR_CONFIG = SRLevelConfig(
    pivot_k=5,
    level_lookback_bars=780,
    cluster_tol=0.0025,
    min_touches=2,
    touch_tol=0.0015,
    atr_period=14,
    stop_atr_mult=0.5,
    min_reward_r=1.5,
    fallback_target_r=2.0,
)


@dataclass(frozen=True)
class SRLevel:
    price: float
    touches: int


@dataclass(frozen=True)
class SRSignal:
    direction: TradeDirection  # BULLISH or BEARISH only, never NEUTRAL
    entry: float
    stop: float
    target: float
    reward_r: float


def _swing_points(bars: Sequence[Bar], k: int) -> tuple[list[bool], list[bool]]:
    """A bar is a confirmed swing high/low if its high/low is the unique
    extreme within k bars on each side. Only indices in [k, len(bars)-k) can
    ever be True — the edges don't have k bars of confirmation on both sides."""
    n = len(bars)
    is_high = [False] * n
    is_low = [False] * n
    for i in range(k, n - k):
        window_h = [bars[j].high for j in range(i - k, i + k + 1)]
        window_l = [bars[j].low for j in range(i - k, i + k + 1)]
        hi, lo = bars[i].high, bars[i].low
        if hi == max(window_h) and window_h.count(hi) == 1:
            is_high[i] = True
        if lo == min(window_l) and window_l.count(lo) == 1:
            is_low[i] = True
    return is_high, is_low


def _cluster_levels(prices: list[float], tol: float, min_touches: int) -> list[SRLevel]:
    if not prices:
        return []
    prices = sorted(prices)
    clusters: list[list[float]] = [[prices[0]]]
    for p in prices[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [SRLevel(price=sum(c) / len(c), touches=len(c)) for c in clusters if len(c) >= min_touches]


def detect_levels(bars: Sequence[Bar], config: SRLevelConfig) -> tuple[list[SRLevel], list[SRLevel]]:
    """Support/resistance levels visible as of the last bar in `bars` —
    purely causal: a swing only counts once it's pivot_k bars old (so it was
    actually confirmable at the time), drawn from the trailing
    level_lookback_bars window before that point. Returns (supports,
    resistances), each sorted by price ascending."""
    n = len(bars)
    confirm_end = n - config.pivot_k
    if confirm_end <= 0:
        return [], []
    window_start = max(0, confirm_end - config.level_lookback_bars)
    is_high, is_low = _swing_points(bars[:confirm_end], config.pivot_k)
    highs = [bars[i].high for i in range(window_start, confirm_end) if is_high[i]]
    lows = [bars[i].low for i in range(window_start, confirm_end) if is_low[i]]
    resistances = _cluster_levels(highs, config.cluster_tol, config.min_touches)
    supports = _cluster_levels(lows, config.cluster_tol, config.min_touches)
    return supports, resistances


def sr_signal(bars: Sequence[Bar], config: SRLevelConfig) -> SRSignal | None:
    """Bounce off support -> long, rejection at resistance -> short, evaluated
    against the last bar in `bars` ("now"). Stop sits config.stop_atr_mult
    ATRs beyond the reacted-to level; target is the next level out in the
    trade's direction, or a fixed R-multiple fallback if none exists. Returns
    None if no level was reacted to this bar, or the trade's reward doesn't
    clear config.min_reward_r."""
    if len(bars) < config.min_bars_required:
        return None

    atr_values = atr(bars, config.atr_period)
    current_atr = atr_values[-1]
    if current_atr != current_atr or current_atr <= 0:  # NaN (still warming up) or zero
        return None

    supports, resistances = detect_levels(bars, config)
    if not supports and not resistances:
        return None

    current = bars[-1]

    touched_support = [
        lvl for lvl in supports if current.low <= lvl.price * (1 + config.touch_tol) and current.close > lvl.price
    ]
    if touched_support:
        level = max(touched_support, key=lambda lvl: lvl.price)  # nearest support at/below price
        entry = current.close
        stop = level.price - config.stop_atr_mult * current_atr
        risk = entry - stop
        if risk > 0:
            targets_above = [lvl.price for lvl in resistances if lvl.price > entry]
            target = min(targets_above) if targets_above else entry + config.fallback_target_r * risk
            reward_r = (target - entry) / risk
            if reward_r >= config.min_reward_r:
                return SRSignal(direction=TradeDirection.BULLISH, entry=entry, stop=stop, target=target, reward_r=reward_r)

    touched_resistance = [
        lvl for lvl in resistances if current.high >= lvl.price * (1 - config.touch_tol) and current.close < lvl.price
    ]
    if touched_resistance:
        level = min(touched_resistance, key=lambda lvl: lvl.price)  # nearest resistance at/above price
        entry = current.close
        stop = level.price + config.stop_atr_mult * current_atr
        risk = stop - entry
        if risk > 0:
            targets_below = [lvl.price for lvl in supports if lvl.price < entry]
            target = max(targets_below) if targets_below else entry - config.fallback_target_r * risk
            reward_r = (entry - target) / risk
            if reward_r >= config.min_reward_r:
                return SRSignal(direction=TradeDirection.BEARISH, entry=entry, stop=stop, target=target, reward_r=reward_r)

    return None
