from dataclasses import dataclass

from .models import FeatureSnapshot

# OANDA pairs are always THREE_THREE (e.g. EUR_USD) -- no equities/options
# symbol matches this shape. Same pattern used to backfill asset_class on
# ml_trade_outcomes (see ml/trade_outcome_schema.py) and to scope
# get_labeled_dataset (see ml/feature_store_repository.py).
FOREX_SYMBOL_PATTERN = r"^[A-Z]{3}_[A-Z]{3}$"


@dataclass(frozen=True)
class PairPerformance:
    symbol: str
    count: int
    win_rate: float
    total_pnl: float
    avg_win: float  # magnitude, 0 if no wins
    avg_loss: float  # magnitude, 0 if no losses
    expectancy: float  # average pnl per trade


@dataclass(frozen=True)
class FactorAgreement:
    """How often a factor's sign matched the trade's eventual direction,
    split by whether the trade won or lost. A factor that agrees with losers
    more than winners is actively hurting the signal rather than helping it
    -- this is the same diagnosis that found unusual_volume anti-correlated
    with forex outcomes (see decision_engine/scoring.py FOREX_WEIGHTS and
    project memory on forex performance). None rates mean no trades had a
    nonzero reading for that factor in that bucket."""

    factor: str
    winner_agreement_rate: float | None
    winner_sample_size: int
    loser_agreement_rate: float | None
    loser_sample_size: int


@dataclass(frozen=True)
class ForexPerformanceReport:
    trade_count: int
    win_rate: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    expectancy: float
    by_pair: list[PairPerformance]
    by_factor: list[FactorAgreement]


def _direction_sign(direction: str) -> int:
    if direction == "bullish":
        return 1
    if direction == "bearish":
        return -1
    return 0


def _factor_sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def build_pair_performance(trades: list[dict]) -> list[PairPerformance]:
    by_symbol: dict[str, list[float]] = {}
    for trade in trades:
        by_symbol.setdefault(trade["symbol"], []).append(trade["pnl"])

    results = [
        PairPerformance(
            symbol=symbol,
            count=len(pnls),
            win_rate=sum(1 for p in pnls if p > 0) / len(pnls),
            total_pnl=sum(pnls),
            avg_win=_avg_magnitude([p for p in pnls if p > 0]),
            avg_loss=_avg_magnitude([p for p in pnls if p <= 0]),
            expectancy=sum(pnls) / len(pnls),
        )
        for symbol, pnls in by_symbol.items()
    ]
    return sorted(results, key=lambda p: p.total_pnl)


def build_factor_agreement(snapshots: list[FeatureSnapshot]) -> list[FactorAgreement]:
    winners = [s for s in snapshots if s.win]
    losers = [s for s in snapshots if s.win is False]
    factor_names = sorted({name for s in snapshots for name in s.factors})

    results = []
    for name in factor_names:
        w_hits = _agreement_hits(winners, name)
        l_hits = _agreement_hits(losers, name)
        results.append(
            FactorAgreement(
                factor=name,
                winner_agreement_rate=(sum(w_hits) / len(w_hits)) if w_hits else None,
                winner_sample_size=len(w_hits),
                loser_agreement_rate=(sum(l_hits) / len(l_hits)) if l_hits else None,
                loser_sample_size=len(l_hits),
            )
        )
    return results


def _agreement_hits(snapshots: list[FeatureSnapshot], factor_name: str) -> list[bool]:
    hits = []
    for s in snapshots:
        value = s.factors.get(factor_name)
        if not value:  # missing or exactly zero -- factor had no directional opinion
            continue
        hits.append(_direction_sign(s.direction) == _factor_sign(value))
    return hits


def _avg_magnitude(pnls: list[float]) -> float:
    return (abs(sum(pnls)) / len(pnls)) if pnls else 0.0


def build_forex_performance_report(trades: list[dict], snapshots: list[FeatureSnapshot]) -> ForexPerformanceReport:
    pnls = [t["pnl"] for t in trades]
    return ForexPerformanceReport(
        trade_count=len(pnls),
        win_rate=(sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else 0.0,
        total_pnl=sum(pnls),
        avg_win=_avg_magnitude([p for p in pnls if p > 0]),
        avg_loss=_avg_magnitude([p for p in pnls if p <= 0]),
        expectancy=(sum(pnls) / len(pnls)) if pnls else 0.0,
        by_pair=build_pair_performance(trades),
        by_factor=build_factor_agreement(snapshots),
    )
