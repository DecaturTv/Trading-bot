"""Shared-capital portfolio simulation.

The per-symbol `BacktestEngine` runs each symbol in isolation with its own
bankroll, so the tournament had to sum non-compounding fixed-notional P&L
across hundreds of symbols — two symbols could "both" deploy capital on the
same day, and a $2,100 bankroll could show a $90k gain.

This replays every symbol's candidate positions against **one** bankroll:

  * positions compete for cash — a signal is skipped if capital is already
    committed or the concurrent-position cap is hit,
  * sizing is fractional-Kelly off the *combined* realized history and scales
    with current equity (so returns compound),
  * P&L is realized on close; while open, a position is held at cost basis
    (marking it every timestamp would need option quotes at arbitrary times —
    the memory cost this whole design avoids). Max hold is ~1 trading day, so
    the mark-to-market lag is small.

Input is the `SimulatedTrade` rows the engine already emits (run per symbol,
one symbol's option data resident at a time); `positions_from_trades` groups
their exit legs back into positions by (symbol, position_id).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from decision_engine.models import TradeDirection
from options.models import StrategyType
from options.strategy_builders import MIN_TRADEABLE_CONTRACT_COST
from risk.kelly import KellySizer
from risk.statistics import compute_trade_statistics

from .models import SimulatedTrade
from .option_quote_source import FFILL, HISTORICAL


@dataclass(frozen=True)
class PositionExit:
    ts: datetime
    value_per_unit: float  # per-contract dollars
    fraction: float  # of the original position closed by this leg; the legs of a position sum to 1.0
    priced_from: str
    is_last: bool


@dataclass(frozen=True)
class CandidatePosition:
    symbol: str
    strategy_type: StrategyType
    direction: TradeDirection
    entry_ts: datetime
    entry_cost_per_unit: float  # per-contract dollars to open
    exits: list[PositionExit]


@dataclass(frozen=True)
class PortfolioConfig:
    starting_equity: float
    kelly_sizer: KellySizer
    max_concurrent_positions: int = 12  # proxy for account / margin limits
    min_contract_cost: float = MIN_TRADEABLE_CONTRACT_COST


@dataclass
class PortfolioResult:
    starting_equity: float
    ending_equity: float
    realized: list[tuple[datetime, float]] = field(default_factory=list)  # (exit ts, leg pnl), in order
    equity_points: list[tuple[datetime, float]] = field(default_factory=list)  # (ts, realized equity)
    positions_taken: int = 0
    positions_skipped_slots: int = 0
    positions_skipped_capital: int = 0
    positions_skipped_untradeable: int = 0
    peak_concurrent: int = 0
    priced_historical: int = 0
    priced_ffill: int = 0
    symbols_traded: set[str] = field(default_factory=set)

    @property
    def total_pnl(self) -> float:
        return self.ending_equity - self.starting_equity

    @property
    def return_pct(self) -> float:
        return self.total_pnl / self.starting_equity if self.starting_equity else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        peak = self.starting_equity
        max_dd = 0.0
        for _, equity in self.equity_points:
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        return max_dd


def positions_from_trades(trades: list[SimulatedTrade]) -> list[CandidatePosition]:
    """Group exit legs (SimulatedTrade rows sharing (symbol, position_id))
    back into whole positions. Rows without entry/exit timestamps — e.g. from
    the Black-Scholes path, which doesn't stamp them — are dropped: the
    portfolio sim needs a timeline."""
    by_pos: dict[tuple[str, int], list[SimulatedTrade]] = {}
    for t in trades:
        if t.entry_ts is None or t.exit_ts is None:
            continue
        by_pos.setdefault((t.symbol, t.position_id), []).append(t)

    out: list[CandidatePosition] = []
    for (symbol, _pid), legs in by_pos.items():
        legs.sort(key=lambda t: t.exit_ts)
        total_qty = sum(leg.qty for leg in legs)
        if total_qty <= 0:
            continue
        exits = [
            PositionExit(
                ts=leg.exit_ts,
                value_per_unit=leg.exit_value_per_unit,
                fraction=leg.qty / total_qty,
                priced_from=leg.priced_from,
                is_last=(i == len(legs) - 1),
            )
            for i, leg in enumerate(legs)
        ]
        first = legs[0]
        out.append(
            CandidatePosition(
                symbol=symbol,
                strategy_type=first.strategy_type,
                direction=first.direction,
                entry_ts=first.entry_ts,
                entry_cost_per_unit=first.entry_cost_per_unit,
                exits=exits,
            )
        )
    return out


@dataclass
class _Held:
    qty: int
    entry_cost_per_unit: float
    remaining_qty: int


# Event kinds; CLOSE sorts before OPEN at an equal timestamp so freed cash can
# be redeployed on the same bar.
_CLOSE, _OPEN = 0, 1


def simulate_portfolio(candidates: list[CandidatePosition], config: PortfolioConfig) -> PortfolioResult:
    events: list[tuple[datetime, int, int, object]] = []
    for idx, c in enumerate(candidates):
        events.append((c.entry_ts, _OPEN, idx, c))
        for leg in c.exits:
            events.append((leg.ts, _CLOSE, idx, leg))
    events.sort(key=lambda e: (e[0], e[1]))

    cash = config.starting_equity
    realized_total = 0.0
    held: dict[int, _Held] = {}
    result = PortfolioResult(starting_equity=config.starting_equity, ending_equity=config.starting_equity)

    for ts, kind, idx, payload in events:
        if kind is _OPEN:
            c: CandidatePosition = payload  # type: ignore[assignment]
            if c.entry_cost_per_unit < config.min_contract_cost:
                result.positions_skipped_untradeable += 1
                continue
            if len(held) >= config.max_concurrent_positions:
                result.positions_skipped_slots += 1
                continue
            stats = compute_trade_statistics([p for _, p in result.realized])
            fraction = config.kelly_sizer.size(stats).position_fraction
            equity_now = config.starting_equity + realized_total
            budget = min(equity_now * fraction, cash)
            qty = int(budget // c.entry_cost_per_unit)
            if qty <= 0:
                result.positions_skipped_capital += 1
                continue
            cash -= qty * c.entry_cost_per_unit
            held[idx] = _Held(qty=qty, entry_cost_per_unit=c.entry_cost_per_unit, remaining_qty=qty)
            result.positions_taken += 1
            result.symbols_traded.add(c.symbol)
            result.peak_concurrent = max(result.peak_concurrent, len(held))
        else:
            leg: PositionExit = payload  # type: ignore[assignment]
            h = held.get(idx)
            if h is None:  # position was skipped at open
                continue
            close_qty = h.remaining_qty if leg.is_last else min(round(h.qty * leg.fraction), h.remaining_qty)
            if close_qty <= 0:
                if leg.is_last:
                    del held[idx]
                continue
            cash += close_qty * leg.value_per_unit
            leg_pnl = close_qty * (leg.value_per_unit - h.entry_cost_per_unit)
            realized_total += leg_pnl
            result.realized.append((ts, leg_pnl))
            result.equity_points.append((ts, config.starting_equity + realized_total))
            if leg.priced_from == HISTORICAL:
                result.priced_historical += 1
            elif leg.priced_from == FFILL:
                result.priced_ffill += 1
            h.remaining_qty -= close_qty
            if h.remaining_qty <= 0 or leg.is_last:
                del held[idx]

    # End of data: the engine force-closes every position, so `held` should be
    # empty. Anything left is returned at cost basis (zero P&L) — its entry
    # capital is still debited from `cash`, so add it back.
    residual_cost = sum(h.remaining_qty * h.entry_cost_per_unit for h in held.values())
    result.ending_equity = cash + residual_cost
    return result
