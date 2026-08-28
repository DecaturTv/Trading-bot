"""Runs every Strategy in the lineup through the real backtest engines over one
shared slice of history and ranks them by realized dollar P&L.

P&L normalization: the platform's single-symbol engines compound equity
trade-over-trade, so summing their raw per-trade P&L across hundreds of
intraday trades explodes geometrically (a $2,100 bankroll "earning" billions).
Instead, each trade is re-scored as its return on capital deployed times a
FIXED per-trade notional (bankroll x the strategy's own sizing fraction) — so
P&L is additive, non-compounding, and comparable across strategies, while a
strategy that deliberately sizes bigger (higher Kelly / risk %) still shows
proportionally bigger swings.

Scope / known simplification: each symbol (or pair) is still backtested
independently — this is NOT a shared-capital portfolio simulation, so two
symbols can "both" deploy the notional on the same day. Every strategy is
scored under the identical simplification, so the ranking between them is fair.
A shared-pool portfolio sim is a follow-on.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from broker.models import Bar
from data.bars_repository import BarsRepository
from decision_engine.scoring import WeightedFactorModel
from risk.kelly import KellySizer
from trade_management.models import TradeManagementConfig

from backtesting.engine import BacktestEngine
from backtesting.forex_engine import ForexBacktestEngine
from backtesting.forex_models import ForexBacktestConfig
from backtesting.models import BacktestConfig

from .strategies import STRATEGIES, Strategy

# The live options loop scans 15Min among its timeframes, and 15Min is the
# shallowest bar in the DB that still yields hundreds of trades per strategy
# (1Hour/1Day are too sparse for the sample to mean anything). Override with
# run_tournament(equities_timeframe=...).
EQUITIES_TIMEFRAME = "15Min"
FOREX_TIMEFRAME = "H1"  # the only forex timeframe ingested
EQUITIES_BANKROLL = 2100.0
FOREX_BANKROLL = 300.0

# Default lookback per market. Forex is H1-only and the engine recomputes
# indicators over the whole growing window each bar (~O(n^2)/pair), so a
# 400-day forex run takes many minutes; 150 keeps it to ~1.5k bars/pair.
EQUITIES_DAYS_DEFAULT = 60
FOREX_DAYS_DEFAULT = 150

Progress = Callable[[str], None]


def _stderr_progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# Matches the live entry loops (config.settings.signal_confirmation_count).
SIGNAL_CONFIRMATION_COUNT = 3
_WARMUP_BARS = 40
_VOL_LOOKBACK = 20
_FOREX_PAIR_RE = r"^[A-Z]{3}_[A-Z]{3}$"


def _normalized_equity_pnl(entry_cost_per_unit: float, qty: int, raw_pnl: float, notional_per_trade: float) -> float:
    """Re-scale one simulated options trade's P&L to a fixed per-trade notional
    so results are additive rather than compounded (see module docstring)."""
    capital_deployed = entry_cost_per_unit * qty
    if capital_deployed <= 0:
        return 0.0
    return (raw_pnl / capital_deployed) * notional_per_trade


def _normalized_forex_pnl(r_multiple: float, notional_per_trade: float) -> float:
    """Forex P&L is r_multiple x risk_dollars; the engine fixes risk_dollars at
    equity-at-entry (compounding). Recompute it off a fixed notional instead."""
    return r_multiple * notional_per_trade


@dataclass(frozen=True)
class StrategyResult:
    name: str
    total_pnl: float
    trade_count: int
    wins: int
    losses: int
    win_rate: float  # in [0, 1]; 0.0 when there were no trades
    avg_win: float  # magnitude, >= 0
    avg_loss: float  # magnitude, >= 0
    max_drawdown_pct: float  # in [0, 1], peak-to-trough on the pooled equity path
    symbols_traded: int
    ending_bankroll: float  # starting bankroll + total_pnl

    @property
    def return_pct(self) -> float:
        start = self.ending_bankroll - self.total_pnl
        return self.total_pnl / start if start else 0.0


@dataclass(frozen=True)
class Leaderboard:
    market: str  # "equities" | "forex"
    timeframe: str
    period_start: datetime
    period_end: datetime
    starting_bankroll: float
    symbol_count: int  # how many symbols/pairs had usable history
    results: list[StrategyResult] = field(default_factory=list)  # sorted, best first

    @property
    def winner(self) -> StrategyResult | None:
        return self.results[0] if self.results else None


def _pooled_drawdown(pnls_in_time_order: Sequence[float], bankroll: float) -> float:
    equity = bankroll
    peak = bankroll
    max_dd = 0.0
    for pnl in pnls_in_time_order:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return max_dd


def _summarize(name: str, trades_with_time: list[tuple[datetime, float]], symbols_traded: int, bankroll: float) -> StrategyResult:
    pnls = [pnl for _, pnl in trades_with_time]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = sum(pnls)
    ordered = [pnl for _, pnl in sorted(trades_with_time, key=lambda x: x[0])]
    return StrategyResult(
        name=name,
        total_pnl=total,
        trade_count=len(pnls),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(pnls) if pnls else 0.0,
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=abs(sum(losses) / len(losses)) if losses else 0.0,
        max_drawdown_pct=_pooled_drawdown(ordered, bankroll),
        symbols_traded=symbols_traded,
        ending_bankroll=bankroll + total,
    )


def run_equities_strategy(
    strategy: Strategy,
    bars_by_symbol: dict[str, list[Bar]],
    bankroll: float = EQUITIES_BANKROLL,
    progress: Progress | None = None,
) -> StrategyResult:
    model = WeightedFactorModel(weights=strategy.weights, min_available_weight_fraction=strategy.min_coverage)
    kelly = KellySizer(kelly_fraction=strategy.equities.kelly_fraction)
    tm_config = TradeManagementConfig(
        stop_loss_pct=strategy.equities.stop_loss_pct,
        profit_target_dollars=strategy.equities.profit_target_dollars,
        trailing_stop_pct=strategy.equities.trailing_stop_pct,
        min_trading_days_before_expiry=2,
        stop_loss_confirmation_count=2,
        reversal_confirmation_count=SIGNAL_CONFIRMATION_COUNT,
        trailing_stop_confirmation_count=2,
        # Shared 2026-08-28 risk baseline — the same values the live config
        # runs (config.settings): never hold past 1 trading day, scale out
        # half at the profit target. All four strategies compete under this.
        max_hold_trading_days=1,
        scale_out_fraction=0.5,
    )
    config = BacktestConfig(
        starting_equity=bankroll,
        confidence_threshold=strategy.equities.confidence_threshold,
        target_delta=strategy.equities.target_delta,
        target_dte=strategy.equities.target_dte,
        volatility_lookback=_VOL_LOOKBACK,
        warmup_bars=_WARMUP_BARS,
        signal_confirmation_count=SIGNAL_CONFIRMATION_COUNT,
    )
    engine = BacktestEngine(model, kelly, tm_config, config)
    notional_per_trade = bankroll * strategy.equities.kelly_fraction

    started = time.monotonic()
    trades_with_time: list[tuple[datetime, float]] = []
    symbols_traded = 0
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < _WARMUP_BARS + _VOL_LOOKBACK + 2:
            continue
        result = engine.run(symbol, bars)
        if result.trades:
            symbols_traded += 1
        for t in result.trades:
            stamp = datetime(t.exit_date.year, t.exit_date.month, t.exit_date.day, tzinfo=timezone.utc)
            pnl = _normalized_equity_pnl(t.entry_cost_per_unit, t.qty, t.pnl, notional_per_trade)
            trades_with_time.append((stamp, pnl))

    summary = _summarize(strategy.name, trades_with_time, symbols_traded, bankroll)
    if progress:
        progress(
            f"  [equities] {strategy.name:<16} {summary.trade_count:>4} trades  "
            f"P&L ${summary.total_pnl:>+11,.2f}  ({time.monotonic() - started:.0f}s)"
        )
    return summary


def run_forex_strategy(
    strategy: Strategy,
    bars_by_pair: dict[str, list[Bar]],
    bankroll: float = FOREX_BANKROLL,
    progress: Progress | None = None,
) -> StrategyResult:
    model = WeightedFactorModel(weights=strategy.weights, min_available_weight_fraction=strategy.min_coverage)
    config = ForexBacktestConfig(
        starting_equity=bankroll,
        confidence_threshold=strategy.forex.confidence_threshold,
        risk_pct_per_trade=strategy.forex.risk_pct_per_trade,
        stop_atr_multiplier=strategy.forex.stop_atr_multiplier,
        take_profit_r_multiple=strategy.forex.take_profit_r_multiple,
        warmup_bars=_WARMUP_BARS,
    )
    engine = ForexBacktestEngine(model, config)
    notional_per_trade = bankroll * strategy.forex.risk_pct_per_trade  # 1R, off the fixed bankroll

    started = time.monotonic()
    trades_with_time: list[tuple[datetime, float]] = []
    pairs_traded = 0
    for pair, bars in bars_by_pair.items():
        if len(bars) < _WARMUP_BARS + config.min_candles_for_signal + 2:
            continue
        result = engine.run(pair, bars)
        if result.trades:
            pairs_traded += 1
        for t in result.trades:
            trades_with_time.append((t.exit_time, _normalized_forex_pnl(t.r_multiple, notional_per_trade)))

    summary = _summarize(strategy.name, trades_with_time, pairs_traded, bankroll)
    if progress:
        progress(
            f"  [forex]    {strategy.name:<16} {summary.trade_count:>4} trades  "
            f"P&L ${summary.total_pnl:>+11,.2f}  ({time.monotonic() - started:.0f}s)"
        )
    return summary


async def _load_bars(
    bars_repo: BarsRepository, symbols: Sequence[str], timeframe: str, start: datetime, end: datetime
) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = {}
    for symbol in symbols:
        bars = await bars_repo.get_bars(symbol, timeframe, start, end)
        if bars:
            out[symbol] = bars
    return out


async def _symbols_for(pool, timeframe: str, forex: bool) -> list[str]:
    if forex:
        sql = f"SELECT DISTINCT symbol FROM bars WHERE timeframe=$1 AND symbol ~ '{_FOREX_PAIR_RE}' ORDER BY symbol"
    else:
        sql = f"SELECT DISTINCT symbol FROM bars WHERE timeframe=$1 AND symbol !~ '{_FOREX_PAIR_RE}' ORDER BY symbol"
    rows = await pool.fetch(sql, timeframe)
    return [r["symbol"] for r in rows]


async def run_tournament(
    pool,
    market: str = "both",
    *,
    equities_days: int = EQUITIES_DAYS_DEFAULT,
    forex_days: int = FOREX_DAYS_DEFAULT,
    equities_timeframe: str = EQUITIES_TIMEFRAME,
    max_symbols: int | None = None,
    strategies: Sequence[Strategy] = tuple(STRATEGIES),
    progress: Progress | None = _stderr_progress,
) -> dict[str, Leaderboard]:
    """market: "equities" | "forex" | "both". Returns one Leaderboard per market
    run. max_symbols caps the universe per market (first N alphabetically) for a
    quick smoke run. Pass progress=None to silence the per-strategy log."""
    bars_repo = BarsRepository(pool)
    now = datetime.now(timezone.utc)
    boards: dict[str, Leaderboard] = {}
    log = progress or (lambda _msg: None)

    if market in ("equities", "both"):
        start = now - timedelta(days=equities_days)
        symbols = await _symbols_for(pool, equities_timeframe, forex=False)
        if max_symbols is not None:
            symbols = symbols[:max_symbols]
        bars_by_symbol = await _load_bars(bars_repo, symbols, equities_timeframe, start, now)
        log(f"equities: {len(bars_by_symbol)} symbols, {equities_timeframe} bars, {equities_days}d lookback")
        results = sorted(
            (run_equities_strategy(s, bars_by_symbol, progress=progress) for s in strategies),
            key=lambda r: r.total_pnl,
            reverse=True,
        )
        boards["equities"] = Leaderboard(
            market="equities",
            timeframe=equities_timeframe,
            period_start=start,
            period_end=now,
            starting_bankroll=EQUITIES_BANKROLL,
            symbol_count=len(bars_by_symbol),
            results=results,
        )

    if market in ("forex", "both"):
        start = now - timedelta(days=forex_days)
        pairs = await _symbols_for(pool, FOREX_TIMEFRAME, forex=True)
        if max_symbols is not None:
            pairs = pairs[:max_symbols]
        bars_by_pair = await _load_bars(bars_repo, pairs, FOREX_TIMEFRAME, start, now)
        log(f"forex: {len(bars_by_pair)} pairs, {FOREX_TIMEFRAME} bars, {forex_days}d lookback")
        results = sorted(
            (run_forex_strategy(s, bars_by_pair, progress=progress) for s in strategies),
            key=lambda r: r.total_pnl,
            reverse=True,
        )
        boards["forex"] = Leaderboard(
            market="forex",
            timeframe=FOREX_TIMEFRAME,
            period_start=start,
            period_end=now,
            starting_bankroll=FOREX_BANKROLL,
            symbol_count=len(bars_by_pair),
            results=results,
        )

    return boards
