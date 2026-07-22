from dataclasses import dataclass


@dataclass(frozen=True)
class TradeStatistics:
    win_rate: float  # in [0, 1]
    avg_win: float  # average gain on winning trades (magnitude, > 0)
    avg_loss: float  # average loss on losing trades (magnitude, > 0)
    sample_size: int


@dataclass(frozen=True)
class KellyResult:
    full_kelly_fraction: float  # f*, clipped to [0, 1]
    position_fraction: float  # what to actually size with, after fraction/fallback/cap
    used_fallback: bool


class KellySizer:
    """Fractional-Kelly position sizing driven by measured win rate / win-loss
    ratio — not a fixed percentage, not escalating after wins (see project
    memory: manual escalation on top of Kelly would push sizing past the
    growth-optimal point, since Kelly already adjusts as edge is measured).
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        min_sample_size: int = 30,
        fallback_fraction: float = 0.02,
        max_position_fraction: float = 1.0,
    ):
        if not 0 < kelly_fraction <= 1:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if not 0 <= fallback_fraction <= 1:
            raise ValueError("fallback_fraction must be in [0, 1]")
        if not 0 <= max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be in [0, 1]")
        self._kelly_fraction = kelly_fraction
        self._min_sample_size = min_sample_size
        self._fallback_fraction = fallback_fraction
        self._max_position_fraction = max_position_fraction

    def size(self, stats: TradeStatistics | None) -> KellyResult:
        # Too little (or no) trade history: win-rate/win-loss estimates would
        # be noise, not edge. Use a small fixed fraction instead of computing
        # a "confident" Kelly size off an unreliable sample.
        if stats is None or stats.sample_size < self._min_sample_size:
            return KellyResult(full_kelly_fraction=0.0, position_fraction=self._fallback_fraction, used_fallback=True)

        if not 0 <= stats.win_rate <= 1:
            raise ValueError("win_rate must be in [0, 1]")
        if stats.avg_loss <= 0:
            raise ValueError("avg_loss must be positive (it's a magnitude, not a signed value)")
        if stats.avg_win <= 0:
            raise ValueError("avg_win must be positive")

        payoff_ratio = stats.avg_win / stats.avg_loss
        full_kelly = stats.win_rate - (1 - stats.win_rate) / payoff_ratio
        full_kelly = max(0.0, min(1.0, full_kelly))  # negative edge -> bet nothing

        position_fraction = min(self._kelly_fraction * full_kelly, self._max_position_fraction)
        return KellyResult(full_kelly_fraction=full_kelly, position_fraction=position_fraction, used_fallback=False)
