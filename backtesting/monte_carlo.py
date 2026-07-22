import random
from dataclasses import dataclass

from .models import SimulatedTrade


@dataclass(frozen=True)
class MonteCarloResult:
    final_equities: list[float]
    max_drawdowns: list[float]  # fraction, e.g. 0.25 = 25% peak-to-trough drawdown


def run_monte_carlo(
    trades: list[SimulatedTrade],
    starting_equity: float,
    num_simulations: int = 1000,
    rng: random.Random | None = None,
) -> MonteCarloResult:
    """Bootstrap-resamples the observed trade PnL sequence (with replacement)
    to build a distribution of equity outcomes consistent with the observed
    win/loss distribution. This resamples trade ORDER, not market data — it
    asks "how much does the outcome depend on the luck of which trade
    happened when," not "what if the market had done something different."
    """
    if not trades:
        raise ValueError("trades must not be empty")
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")
    if num_simulations < 1:
        raise ValueError("num_simulations must be >= 1")

    rng = rng or random.Random()
    pnls = [t.pnl for t in trades]

    final_equities = []
    max_drawdowns = []
    for _ in range(num_simulations):
        resampled = rng.choices(pnls, k=len(pnls))
        equity = starting_equity
        peak = starting_equity
        max_dd = 0.0
        for pnl in resampled:
            equity += pnl
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        final_equities.append(equity)
        max_drawdowns.append(max_dd)

    return MonteCarloResult(final_equities=final_equities, max_drawdowns=max_drawdowns)
