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

Two guards keep the synthetic options pricer from blowing that up (forex has
no equivalent problem — its per-trade P&L is r_multiple x notional, and
r_multiple is bounded by the take-profit / stop):

  * simulated_strategy_value is a Black-Scholes stub with no real chain or
    liquidity behind it, so it happily prices a hopeless deep-OTM contract at
    a fraction of a cent. The engine then "buys" millions of them
    (qty = budget // entry_cost) and a single tick reprices the lot into the
    billions. Trades entered below _MIN_TRADEABLE_CONTRACT_COST are dropped —
    the live loop prices against a real chain and would never size or fill
    one of these.
  * Even above that floor, a cheap option's return on capital is effectively
    unbounded on the upside (a $0.02 contract going to $2 is +9,900%). Return
    on capital is clamped to [_ROC_FLOOR, _ROC_CAP] before scaling to the
    notional so no single trade can dominate a many-thousand-trade aggregate
    — the options analog of forex's r_multiple bound.

Dropping the sub-floor trades can bias the mix slightly (a strategy that
targets cheaper options loses more of them), but every strategy gets the same
filter, and a ranking built on 1e14-contract phantom fills is worse.

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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from broker.models import Bar
from data.bars_repository import BarsRepository
from data.option_bars_repository import OptionBarsRepository
from decision_engine.scoring import WeightedFactorModel
from options.strategy_builders import MIN_TRADEABLE_CONTRACT_COST
from risk.kelly import KellySizer
from trade_management.models import TradeManagementConfig

from backtesting.engine import BacktestEngine
from backtesting.forex_engine import ForexBacktestEngine
from backtesting.forex_models import ForexBacktestConfig
from backtesting.models import BacktestConfig
from backtesting.option_quote_source import FFILL, HISTORICAL, build_historical_quote_source
from backtesting.portfolio_sim import PortfolioConfig, positions_from_trades, simulate_portfolio

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


# simulated_strategy_value returns a Black-Scholes price * 100, i.e. per-contract
# dollars. Below ~$1/contract ($0.01/share) the stub is just noise — no real
# broker fills a sub-penny option, and the engine's qty = budget // entry_cost
# turns it into a 1e6–1e14-contract phantom position. Trades entered cheaper
# than this are dropped from the tournament entirely. Same floor the live entry
# loop enforces (dashboard/trading_loop.py) — kept as one shared constant.
_MIN_TRADEABLE_CONTRACT_COST = MIN_TRADEABLE_CONTRACT_COST

# A long debit option/spread loses at most the premium paid (-100%); its upside
# is convex but a single trade returning thousands of percent is the synthetic
# pricer misbehaving on a near-zero premium, not signal. Clamp return-on-capital
# to this band before scaling to the fixed notional so one outlier can't swamp
# the sum. +10 still records a +1,000% trade as a large winner.
_ROC_FLOOR = -1.0
_ROC_CAP = 10.0


def _normalized_equity_pnl(entry_cost_per_unit: float, qty: int, raw_pnl: float, notional_per_trade: float) -> float:
    """Re-scale one simulated options trade's P&L to a fixed per-trade notional
    so results are additive rather than compounded (see module docstring), with
    return-on-capital clamped to [_ROC_FLOOR, _ROC_CAP] so a single
    synthetic-pricing outlier can't dominate the sum."""
    capital_deployed = entry_cost_per_unit * qty
    if capital_deployed <= 0:
        return 0.0
    roc = raw_pnl / capital_deployed
    roc = max(_ROC_FLOOR, min(_ROC_CAP, roc))
    return roc * notional_per_trade


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
    # Real-quote coverage (0 unless run against HistoricalOptionQuoteSource):
    priced_historical: int = 0  # exits priced from a real option bar at the timestamp
    priced_ffill: int = 0  # exits priced from a forward-filled real bar
    entries_skipped_no_quote: int = 0  # signals dropped because no real option bar was near
    symbols_without_option_data: int = 0
    # Shared-capital portfolio sim (0 unless run via run_equities_portfolio_tournament):
    positions_taken: int = 0
    positions_skipped_capital: int = 0  # signal fired but capital was committed elsewhere
    positions_skipped_slots: int = 0  # signal fired but the concurrent-position cap was hit
    peak_concurrent: int = 0

    @property
    def return_pct(self) -> float:
        start = self.ending_bankroll - self.total_pnl
        return self.total_pnl / start if start else 0.0

    @property
    def real_quote_pct(self) -> float:
        priced = self.priced_historical + self.priced_ffill
        return self.priced_historical / priced if priced else 0.0


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


def _summarize(
    name: str,
    trades_with_time: list[tuple[datetime, float]],
    symbols_traded: int,
    bankroll: float,
    coverage: dict[str, int] | None = None,
) -> StrategyResult:
    pnls = [pnl for _, pnl in trades_with_time]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = sum(pnls)
    ordered = [pnl for _, pnl in sorted(trades_with_time, key=lambda x: x[0])]
    cov = coverage or {}
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
        priced_historical=cov.get("historical", 0),
        priced_ffill=cov.get("ffill", 0),
        entries_skipped_no_quote=cov.get("skipped", 0),
        symbols_without_option_data=cov.get("no_data_symbols", 0),
    )


def _equities_backtest_setup(strategy: Strategy, bankroll: float):
    """The (model, kelly, trade-management, backtest) config a strategy runs
    under — shared by the synthetic-pricing path (run_equities_strategy) and
    the real-quote path (run_equities_tournament_with_history)."""
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
    notional_per_trade = bankroll * strategy.equities.kelly_fraction
    return model, kelly, tm_config, config, notional_per_trade


def run_equities_strategy(
    strategy: Strategy,
    bars_by_symbol: dict[str, list[Bar]],
    bankroll: float = EQUITIES_BANKROLL,
    progress: Progress | None = None,
) -> StrategyResult:
    """Synthetic (Black-Scholes) options pricing — the engine default. Kept
    for tests and ad-hoc use; the tournament proper now runs the real-quote
    path (run_equities_tournament_with_history)."""
    model, kelly, tm_config, config, notional_per_trade = _equities_backtest_setup(strategy, bankroll)
    engine = BacktestEngine(model, kelly, tm_config, config)

    started = time.monotonic()
    trades_with_time: list[tuple[datetime, float]] = []
    symbols_traded = 0
    dropped_untradeable = 0
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < _WARMUP_BARS + _VOL_LOOKBACK + 2:
            continue
        result = engine.run(symbol, bars)
        symbol_had_trade = False
        for t in result.trades:
            if t.entry_cost_per_unit < _MIN_TRADEABLE_CONTRACT_COST:
                dropped_untradeable += 1
                continue
            stamp = datetime(t.exit_date.year, t.exit_date.month, t.exit_date.day, tzinfo=timezone.utc)
            pnl = _normalized_equity_pnl(t.entry_cost_per_unit, t.qty, t.pnl, notional_per_trade)
            trades_with_time.append((stamp, pnl))
            symbol_had_trade = True
        if symbol_had_trade:
            symbols_traded += 1

    summary = _summarize(strategy.name, trades_with_time, symbols_traded, bankroll)
    if progress:
        dropped_note = f"  (dropped {dropped_untradeable} sub-${_MIN_TRADEABLE_CONTRACT_COST:g} synthetic)" if dropped_untradeable else ""
        progress(
            f"  [equities] {strategy.name:<16} {summary.trade_count:>4} trades  "
            f"P&L ${summary.total_pnl:>+11,.2f}  ({time.monotonic() - started:.0f}s){dropped_note}"
        )
    return summary


async def run_equities_tournament_with_history(
    strategies: Sequence[Strategy],
    bars_by_symbol: dict[str, list[Bar]],
    option_repo: OptionBarsRepository,
    timeframe: str,
    start: datetime,
    end: datetime,
    bankroll: float = EQUITIES_BANKROLL,
    progress: Progress | None = None,
    max_ffill_bars: int = 3,
) -> list[StrategyResult]:
    """Runs every strategy over the shared symbol set, pricing options from
    real historical bars (`option_bars`). Memory-frugal: loads one symbol's
    option series at a time, runs all strategies on it, then releases it —
    the box this runs on has ~1 GB RAM.

    Returns results sorted by total P&L, best first. Raises if `option_bars`
    holds nothing for the universe (run backtesting.ingest_option_history)."""
    exp_gte = start.date()
    exp_lte = end.date() + timedelta(days=100)

    setups = {s.name: _equities_backtest_setup(s, bankroll) for s in strategies}
    trades: dict[str, list[tuple[datetime, float]]] = {s.name: [] for s in strategies}
    traded_symbols: dict[str, set[str]] = {s.name: set() for s in strategies}
    cov: dict[str, dict[str, int]] = {s.name: {"historical": 0, "ffill": 0, "skipped": 0} for s in strategies}
    no_data_symbols = 0
    priced_any = False

    started = time.monotonic()
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < _WARMUP_BARS + _VOL_LOOKBACK + 2:
            continue
        quotes = await build_historical_quote_source(
            option_repo, symbol, timeframe, start, end, exp_gte, exp_lte, max_ffill_bars=max_ffill_bars
        )
        if quotes.contract_count == 0:
            no_data_symbols += 1
            continue
        priced_any = True

        for s in strategies:
            model, kelly, tm_config, config, notional = setups[s.name]
            engine = BacktestEngine(model, kelly, tm_config, config, quote_source=quotes)
            result = engine.run(symbol, bars)
            cov[s.name]["skipped"] += result.entries_skipped_no_quote
            for t in result.trades:
                if t.entry_cost_per_unit < _MIN_TRADEABLE_CONTRACT_COST:
                    continue
                cov[s.name][t.priced_from] = cov[s.name].get(t.priced_from, 0) + 1
                stamp = datetime(t.exit_date.year, t.exit_date.month, t.exit_date.day, tzinfo=timezone.utc)
                trades[s.name].append((stamp, _normalized_equity_pnl(t.entry_cost_per_unit, t.qty, t.pnl, notional)))
                traded_symbols[s.name].add(symbol)
        del quotes

    if not priced_any:
        raise RuntimeError(
            "no option history found for the equities universe — run "
            "`python -m backtesting.ingest_option_history --underlyings tournament "
            f"--start {start.date()} --end {end.date()} --timeframe {timeframe}` first"
        )

    results = []
    for s in strategies:
        c = cov[s.name]
        summary = _summarize(
            s.name, trades[s.name], len(traded_symbols[s.name]), bankroll,
            coverage={**c, "no_data_symbols": no_data_symbols},
        )
        results.append(summary)
        if progress:
            progress(
                f"  [equities] {s.name:<16} {summary.trade_count:>4} trades  "
                f"P&L ${summary.total_pnl:>+11,.2f}  "
                f"real {c['historical']}/ffill {c['ffill']}/skip {c['skipped']}  "
                f"({time.monotonic() - started:.0f}s)"
            )
    return sorted(results, key=lambda r: r.total_pnl, reverse=True)


# Concurrent-position cap for the shared-capital sim — a proxy for what one
# options account can realistically carry at once (margin, attention, the
# live loop's own per-name exposure checks). Capital is the other limiter.
PORTFOLIO_MAX_CONCURRENT = 12


async def run_equities_portfolio_tournament(
    strategies: Sequence[Strategy],
    bars_by_symbol: dict[str, list[Bar]],
    option_repo: OptionBarsRepository,
    timeframe: str,
    start: datetime,
    end: datetime,
    bankroll: float = EQUITIES_BANKROLL,
    progress: Progress | None = None,
    max_ffill_bars: int = 3,
    max_concurrent: int = PORTFOLIO_MAX_CONCURRENT,
) -> list[StrategyResult]:
    """Real option quotes AND one shared bankroll: every symbol's candidate
    positions replay against a single account (see backtesting.portfolio_sim)
    — positions compete for capital, sizing compounds with equity. This is
    the number to trust; run_equities_tournament_with_history's per-symbol
    pools inflate returns.

    Memory-frugal like run_equities_tournament_with_history: one symbol's
    option data resident at a time. Raises if `option_bars` is empty."""
    exp_gte = start.date()
    exp_lte = end.date() + timedelta(days=100)

    setups = {s.name: _equities_backtest_setup(s, bankroll) for s in strategies}
    raw_trades: dict[str, list] = {s.name: [] for s in strategies}
    entries_skipped: dict[str, int] = {s.name: 0 for s in strategies}
    no_data_symbols = 0
    priced_any = False

    started = time.monotonic()
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < _WARMUP_BARS + _VOL_LOOKBACK + 2:
            continue
        quotes = await build_historical_quote_source(
            option_repo, symbol, timeframe, start, end, exp_gte, exp_lte, max_ffill_bars=max_ffill_bars
        )
        if quotes.contract_count == 0:
            no_data_symbols += 1
            continue
        priced_any = True
        for s in strategies:
            model, kelly, tm_config, config, _notional = setups[s.name]
            result = BacktestEngine(model, kelly, tm_config, config, quote_source=quotes).run(symbol, bars)
            entries_skipped[s.name] += result.entries_skipped_no_quote
            raw_trades[s.name].extend(result.trades)
        del quotes

    if not priced_any:
        raise RuntimeError(
            "no option history found for the equities universe — run "
            "`python -m backtesting.ingest_option_history --underlyings tournament "
            f"--start {start.date()} --end {end.date()} --timeframe {timeframe}` first"
        )

    results: list[StrategyResult] = []
    for s in strategies:
        # Fresh Kelly sizer for the portfolio: same fractional-Kelly multiplier
        # as the strategy, that multiplier as the small-sample fallback, and a
        # hard per-position cap so one position can't take the whole account.
        portfolio_kelly = KellySizer(
            kelly_fraction=s.equities.kelly_fraction,
            fallback_fraction=s.equities.kelly_fraction,
            max_position_fraction=0.34,
        )
        candidates = positions_from_trades(raw_trades[s.name])
        sim = simulate_portfolio(
            candidates,
            PortfolioConfig(
                starting_equity=bankroll, kelly_sizer=portfolio_kelly, max_concurrent_positions=max_concurrent
            ),
        )
        summary = _summarize(
            s.name, sim.realized, len(sim.symbols_traded), bankroll,
            coverage={
                "historical": sim.priced_historical, "ffill": sim.priced_ffill,
                "skipped": entries_skipped[s.name], "no_data_symbols": no_data_symbols,
            },
        )
        summary = replace(
            summary,
            positions_taken=sim.positions_taken,
            positions_skipped_capital=sim.positions_skipped_capital,
            positions_skipped_slots=sim.positions_skipped_slots,
            peak_concurrent=sim.peak_concurrent,
        )
        results.append(summary)
        if progress:
            progress(
                f"  [equities] {s.name:<16} {sim.positions_taken:>4} pos  "
                f"P&L ${sim.total_pnl:>+11,.2f} ({sim.return_pct:+.1%})  "
                f"DD {sim.max_drawdown_pct:.1%}  peak {sim.peak_concurrent} concurrent  "
                f"skip cap/slot {sim.positions_skipped_capital}/{sim.positions_skipped_slots}  "
                f"({time.monotonic() - started:.0f}s)"
            )
    return sorted(results, key=lambda r: r.total_pnl, reverse=True)


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
    equities_bankroll: float = EQUITIES_BANKROLL,
    max_symbols: int | None = None,
    strategies: Sequence[Strategy] = tuple(STRATEGIES),
    progress: Progress | None = _stderr_progress,
) -> dict[str, Leaderboard]:
    """market: "equities" | "forex" | "both". Returns one Leaderboard per market
    run. max_symbols caps the universe per market (first N alphabetically) for a
    quick smoke run. equities_bankroll is the single shared account the
    portfolio sim sizes against — raise it above the $2,100 paper split to give
    the strategy comparison a real sample (on $2,100 only ~1-2 option positions
    fit at once). Pass progress=None to silence the per-strategy log."""
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
        results = await run_equities_portfolio_tournament(
            strategies, bars_by_symbol, OptionBarsRepository(pool), equities_timeframe, start, now,
            bankroll=equities_bankroll, progress=progress,
        )
        boards["equities"] = Leaderboard(
            market="equities",
            timeframe=equities_timeframe,
            period_start=start,
            period_end=now,
            starting_bankroll=equities_bankroll,
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
