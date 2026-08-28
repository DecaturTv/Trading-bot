from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from broker.models import Bar, OptionRight
from decision_engine.confirmation import is_confirmed, update_streak
from decision_engine.models import TradeDirection
from decision_engine.scoring import WeightedFactorModel
from options.models import StrategyType
from risk.kelly import KellySizer
from risk.sizing import contracts_for_budget, position_budget_dollars
from scanner.scans import scan_gap, scan_momentum, scan_unusual_volume
from trade_management.exit_rules import evaluate_exit
from trade_management.expiry import trading_days_until
from trade_management.models import ExitAction, PositionState, TradeManagementConfig

from .models import BacktestConfig, BacktestResult, SimulatedTrade
from .option_quote_source import OptionQuoteSource, SimulatedOptionQuoteSource
from .simulated_pricing import SimulatedLeg
from .statistics import compute_trade_statistics
from .volatility_estimator import realized_volatility

_SCAN_FUNCTIONS = (scan_unusual_volume, scan_gap, scan_momentum)


@dataclass
class _OpenPosition:
    legs: list[SimulatedLeg]
    expiration: date
    strategy_type: StrategyType
    direction: TradeDirection
    entry_date: date
    state: PositionState


def _target_dte_to_expiration(as_of: date, target_dte_trading_days: int) -> date:
    # Approximate: ~5 trading days per 7 calendar days.
    calendar_days = round(target_dte_trading_days * 7 / 5)
    return as_of + timedelta(days=calendar_days)


class BacktestEngine:
    """Replays historical bars through the same scanner/decision_engine/
    trade_management logic used live — not a reimplementation of the
    strategy, the actual pure functions — so backtest and live behavior
    can't silently diverge.

    Option pricing comes from the injected `quote_source`
    (`backtesting.option_quote_source`): the default `SimulatedOptionQuoteSource`
    is the Black-Scholes stub; `HistoricalOptionQuoteSource` marks against real
    historical option bars and returns None when a contract has no bar near a
    timestamp, in which case the engine skips the entry or holds the position.

    Single symbol, single open position at a time (no pyramiding) — a
    multi-symbol portfolio backtest sharing one capital pool is a natural
    extension not built here.
    """

    def __init__(
        self,
        decision_model: WeightedFactorModel,
        kelly_sizer: KellySizer,
        trade_management_config: TradeManagementConfig,
        config: BacktestConfig,
        quote_source: OptionQuoteSource | None = None,
    ):
        self._decision_model = decision_model
        self._kelly_sizer = kelly_sizer
        self._tm_config = trade_management_config
        self._config = config
        self._quotes = quote_source or SimulatedOptionQuoteSource(config.risk_free_rate)

    def run(self, symbol: str, bars: Sequence[Bar]) -> BacktestResult:
        equity = self._config.starting_equity
        equity_curve: list[float] = []
        trades: list[SimulatedTrade] = []
        open_position: _OpenPosition | None = None
        last_bar_seen: Bar | None = None
        last_vol: float | None = None
        confirmation_direction: TradeDirection | None = None
        confirmation_streak = 0
        entries_skipped_no_quote = 0

        for i in range(self._config.warmup_bars, len(bars)):
            window = bars[: i + 1]
            current_bar = bars[i]
            as_of = current_bar.timestamp.date()
            vol = realized_volatility(window, lookback=self._config.volatility_lookback)
            if vol is None:
                continue
            last_vol = vol
            last_bar_seen = current_bar

            if open_position is not None:
                equity, open_position = self._process_open_position(
                    symbol, open_position, current_bar, as_of, vol, equity, trades
                )
                if open_position is None:
                    equity_curve.append(equity)
                continue

            open_position, confirmation_direction, confirmation_streak, skipped = self._maybe_enter(
                symbol, window, current_bar, as_of, vol, equity, trades, confirmation_direction, confirmation_streak
            )
            entries_skipped_no_quote += skipped

        if open_position is not None and last_bar_seen is not None and last_vol is not None:
            equity = self._force_close(symbol, open_position, last_bar_seen, last_vol, equity, trades)
            equity_curve.append(equity)

        return BacktestResult(
            symbol=symbol,
            trades=trades,
            equity_curve=equity_curve,
            starting_equity=self._config.starting_equity,
            ending_equity=equity,
            entries_skipped_no_quote=entries_skipped_no_quote,
        )

    def _process_open_position(self, symbol, open_position, current_bar, as_of, vol, equity, trades):
        mark = self._quotes.mark(open_position.legs, current_bar.close, current_bar.timestamp, vol)
        if mark is None:
            # No real quote near this bar — can't evaluate an exit; hold and
            # try again next bar (same effect as ExitAction.NONE).
            return equity, open_position
        current_value = mark.value_per_unit

        dte = trading_days_until(open_position.expiration, as_of)
        days_held = trading_days_until(as_of, open_position.entry_date)
        decision = evaluate_exit(
            open_position.state, current_value, dte, self._tm_config, trading_days_held=days_held
        )

        if decision.action is ExitAction.NONE:
            if decision.stop_loss_streak != open_position.state.stop_loss_streak:
                open_position.state = replace(open_position.state, stop_loss_streak=decision.stop_loss_streak)
            return equity, open_position

        closed_qty = decision.qty_to_close
        pnl = (current_value - open_position.state.entry_cost_per_unit) * closed_qty
        trades.append(
            SimulatedTrade(
                symbol=symbol,
                strategy_type=open_position.strategy_type,
                direction=open_position.direction,
                entry_date=open_position.entry_date,
                exit_date=as_of,
                entry_cost_per_unit=open_position.state.entry_cost_per_unit,
                exit_value_per_unit=current_value,
                qty=closed_qty,
                exit_reason=decision.action.value,
                pnl=pnl,
                priced_from=mark.source,
            )
        )
        equity += pnl

        remaining = open_position.state.qty - closed_qty
        if remaining <= 0:
            return equity, None

        current_gain_pct = (current_value - open_position.state.entry_cost_per_unit) / open_position.state.entry_cost_per_unit
        peak = max(open_position.state.peak_gain_pct, current_gain_pct)
        open_position.state = replace(
            open_position.state, qty=remaining, scaled_out=True, peak_gain_pct=peak, stop_loss_streak=decision.stop_loss_streak
        )
        return equity, open_position

    def _maybe_enter(self, symbol, window, current_bar, as_of, vol, equity, trades, confirmation_direction, confirmation_streak):
        scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, window)) is not None]
        signal = self._decision_model.score(symbol, window, scan_hits, self._config.confidence_threshold)
        if not signal.meets_threshold or signal.direction is TradeDirection.NEUTRAL:
            return None, None, 0, 0

        # Require the signal to hold for signal_confirmation_count consecutive
        # bars before acting on it, same as the live entry loops (see
        # dashboard/trading_loop.py) -- otherwise a single noisy bar can open
        # (and immediately stop out of) a position the live strategy would
        # never have entered, silently diverging backtest from live behavior.
        streak = update_streak(signal.direction, confirmation_direction, confirmation_streak)
        if not is_confirmed(streak, self._config.signal_confirmation_count):
            return None, signal.direction, streak, 0

        right = OptionRight.CALL if signal.direction is TradeDirection.BULLISH else OptionRight.PUT
        target_expiration = _target_dte_to_expiration(as_of, self._config.target_dte)
        leg = self._quotes.select_leg(
            underlying=symbol,
            as_of=current_bar.timestamp,
            target_expiration=target_expiration,
            right=right,
            target_delta=self._config.target_delta,
            underlying_price=current_bar.close,
            volatility=vol,
        )
        if leg is None:
            return None, signal.direction, streak, 1

        entry_mark = self._quotes.mark([leg], current_bar.close, current_bar.timestamp, vol)
        if entry_mark is None or entry_mark.value_per_unit <= 0:
            return None, signal.direction, streak, 1
        entry_cost = entry_mark.value_per_unit

        stats = compute_trade_statistics(trades)
        kelly_result = self._kelly_sizer.size(stats)
        budget = position_budget_dollars(equity, kelly_result)
        qty = contracts_for_budget(budget, entry_cost)
        if qty <= 0:
            return None, signal.direction, streak, 0

        position = _OpenPosition(
            legs=[leg],
            expiration=leg.expiration,
            strategy_type=StrategyType.LONG_CALL if right is OptionRight.CALL else StrategyType.LONG_PUT,
            direction=signal.direction,
            entry_date=as_of,
            state=PositionState(symbol=symbol, qty=qty, entry_cost_per_unit=entry_cost, scaled_out=False, peak_gain_pct=0.0),
        )
        return position, None, 0, 0

    def _force_close(self, symbol, open_position, last_bar, vol, equity, trades):
        mark = self._quotes.mark(open_position.legs, last_bar.close, last_bar.timestamp, vol)
        if mark is None:
            mark = self._quotes.last_mark(open_position.legs, last_bar.close, last_bar.timestamp, vol)
        if mark is None:
            # Legs were never priceable at all — close flat rather than invent a value.
            current_value = open_position.state.entry_cost_per_unit
            source = "ffill"
        else:
            current_value = mark.value_per_unit
            source = mark.source

        pnl = (current_value - open_position.state.entry_cost_per_unit) * open_position.state.qty
        trades.append(
            SimulatedTrade(
                symbol=symbol,
                strategy_type=open_position.strategy_type,
                direction=open_position.direction,
                entry_date=open_position.entry_date,
                exit_date=last_bar.timestamp.date(),
                entry_cost_per_unit=open_position.state.entry_cost_per_unit,
                exit_value_per_unit=current_value,
                qty=open_position.state.qty,
                exit_reason="end_of_data",
                pnl=pnl,
                priced_from=source,
            )
        )
        return equity + pnl
