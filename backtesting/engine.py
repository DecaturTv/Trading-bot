from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from broker.models import Bar, OptionRight, OrderSide
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
from .simulated_pricing import (
    SimulatedLeg,
    build_synthetic_chain,
    select_synthetic_strike_by_delta,
    simulated_strategy_value,
)
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


def _strike_increment(price: float) -> float:
    if price < 25:
        return 0.5
    if price < 200:
        return 1.0
    return 5.0


class BacktestEngine:
    """Replays historical bars through the same scanner/decision_engine/
    trade_management logic used live — not a reimplementation of the
    strategy, the actual pure functions — so backtest and live behavior
    can't silently diverge. Options pricing is Black-Scholes-simulated
    (see simulated_pricing.py); this project has no historical options
    market data to replay instead.

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
    ):
        self._decision_model = decision_model
        self._kelly_sizer = kelly_sizer
        self._tm_config = trade_management_config
        self._config = config

    def run(self, symbol: str, bars: Sequence[Bar]) -> BacktestResult:
        equity = self._config.starting_equity
        equity_curve: list[float] = []
        trades: list[SimulatedTrade] = []
        open_position: _OpenPosition | None = None
        last_vol: float | None = None

        for i in range(self._config.warmup_bars, len(bars)):
            window = bars[: i + 1]
            current_bar = bars[i]
            as_of = current_bar.timestamp.date()
            vol = realized_volatility(window, lookback=self._config.volatility_lookback)
            if vol is None:
                continue
            last_vol = vol

            if open_position is not None:
                equity, open_position = self._process_open_position(
                    symbol, open_position, current_bar, as_of, vol, equity, trades
                )
                if open_position is None:
                    equity_curve.append(equity)
                continue

            open_position = self._maybe_enter(symbol, window, current_bar, as_of, vol, equity, trades)

        if open_position is not None and last_vol is not None:
            equity = self._force_close(
                symbol, open_position, bars[-1], bars[-1].timestamp.date(), last_vol, equity, trades
            )
            equity_curve.append(equity)

        return BacktestResult(
            symbol=symbol,
            trades=trades,
            equity_curve=equity_curve,
            starting_equity=self._config.starting_equity,
            ending_equity=equity,
        )

    def _process_open_position(self, symbol, open_position, current_bar, as_of, vol, equity, trades):
        current_value = simulated_strategy_value(
            open_position.legs, current_bar.close, as_of, vol, self._config.risk_free_rate
        )
        dte = trading_days_until(open_position.expiration, as_of)
        decision = evaluate_exit(open_position.state, current_value, dte, self._tm_config)

        if decision.action is ExitAction.NONE:
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
            )
        )
        equity += pnl

        remaining = open_position.state.qty - closed_qty
        if remaining <= 0:
            return equity, None

        current_gain_pct = (current_value - open_position.state.entry_cost_per_unit) / open_position.state.entry_cost_per_unit
        peak = max(open_position.state.peak_gain_pct, current_gain_pct)
        open_position.state = replace(open_position.state, qty=remaining, scaled_out=True, peak_gain_pct=peak)
        return equity, open_position

    def _maybe_enter(self, symbol, window, current_bar, as_of, vol, equity, trades):
        scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(symbol, window)) is not None]
        signal = self._decision_model.score(symbol, window, scan_hits, self._config.confidence_threshold)
        if not signal.meets_threshold or signal.direction is TradeDirection.NEUTRAL:
            return None

        right = OptionRight.CALL if signal.direction is TradeDirection.BULLISH else OptionRight.PUT
        expiration = _target_dte_to_expiration(as_of, self._config.target_dte)
        chain = build_synthetic_chain(
            current_bar.close, expiration, as_of, vol, right, strike_increment=_strike_increment(current_bar.close)
        )
        target_delta = self._config.target_delta if right is OptionRight.CALL else -self._config.target_delta
        strike = select_synthetic_strike_by_delta(chain, target_delta)
        leg = SimulatedLeg(strike=strike, expiration=expiration, right=right, side=OrderSide.BUY)

        entry_cost = simulated_strategy_value([leg], current_bar.close, as_of, vol, self._config.risk_free_rate)
        if entry_cost <= 0:
            return None

        stats = compute_trade_statistics(trades)
        kelly_result = self._kelly_sizer.size(stats)
        budget = position_budget_dollars(equity, kelly_result)
        qty = contracts_for_budget(budget, entry_cost)
        if qty <= 0:
            return None

        return _OpenPosition(
            legs=[leg],
            expiration=expiration,
            strategy_type=StrategyType.LONG_CALL if right is OptionRight.CALL else StrategyType.LONG_PUT,
            direction=signal.direction,
            entry_date=as_of,
            state=PositionState(symbol=symbol, qty=qty, entry_cost_per_unit=entry_cost, scaled_out=False, peak_gain_pct=0.0),
        )

    def _force_close(self, symbol, open_position, last_bar, as_of, vol, equity, trades):
        current_value = simulated_strategy_value(
            open_position.legs, last_bar.close, as_of, vol, self._config.risk_free_rate
        )
        pnl = (current_value - open_position.state.entry_cost_per_unit) * open_position.state.qty
        trades.append(
            SimulatedTrade(
                symbol=symbol,
                strategy_type=open_position.strategy_type,
                direction=open_position.direction,
                entry_date=open_position.entry_date,
                exit_date=as_of,
                entry_cost_per_unit=open_position.state.entry_cost_per_unit,
                exit_value_per_unit=current_value,
                qty=open_position.state.qty,
                exit_reason="end_of_data",
                pnl=pnl,
            )
        )
        return equity + pnl
