from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from broker.models import Bar
from decision_engine.models import TradeDirection
from decision_engine.scoring import WeightedFactorModel
from indicators.volatility import atr
from scanner.scans import scan_gap, scan_momentum, scan_unusual_volume

from .forex_models import ForexBacktestConfig, ForexBacktestResult, SimulatedForexTrade

_SCAN_FUNCTIONS = (scan_unusual_volume, scan_gap, scan_momentum)


@dataclass(frozen=True)
class _OpenForexPosition:
    direction: TradeDirection
    entry_time: datetime
    entry_price: float
    stop_price: float  # ratchets favorably only, mirrors OANDA's trailingStopLossOnFill
    stop_distance: float  # fixed at entry -- how far behind the favorable extreme the stop trails
    take_profit_price: float
    risk_dollars: float  # dollar value of 1R, sized once at entry against equity then, not re-sized mid-trade


class ForexBacktestEngine:
    """Replays historical bars through the same scan/decision_engine logic
    forex_loop.py runs live (WeightedFactorModel with FOREX_WEIGHTS, same ATR-
    based stop distance, same scan functions), then simulates OANDA's
    server-side exit model bar-by-bar: a trailing stop that only ever moves
    favorably (see oanda_adapter.submit_market_order's trailingStopLossOnFill)
    plus a fixed take-profit price -- not the options side's trade_management
    scale-out/percent-gain state machine, which forex doesn't use live.

    P&L is tracked in R-multiples (multiples of the initial stop distance)
    rather than real broker units/currency conversion -- deliberately out of
    scope for v1. Replaying forex/conversion.py's quote_to_account_rate
    historically would mean sourcing and pairing historical cross-rate data
    for every quote currency against every backtest period, a project of its
    own; R-multiples answer the actual question this exists for ("does the
    entry signal have edge") without it. Dollar pnl is the realized R
    multiple applied to risk_pct_per_trade against equity AT ENTRY, so the
    equity curve still compounds the way live sizing intends.

    Single pair, single open position at a time -- same scope limit as the
    options BacktestEngine (see its docstring); no currency-concentration
    cap (forex/exposure.py) since a single-pair run has no portfolio to
    concentrate within.

    Entry executes on the same bar the signal was scored against (current
    bar's close stands in for the live ask/bid fill -- candles have no
    bid/ask spread) -- same convention the options engine already uses.
    Exit checks start the FOLLOWING bar, so a position is never entered and
    exited within one bar. Within a bar, if both stop and target fall inside
    its high-low range, stop-loss is checked first -- the conservative
    assumption standard in bar-based backtesting, since OHLC data alone
    can't say which was actually touched first intrabar.
    """

    def __init__(self, decision_model: WeightedFactorModel, config: ForexBacktestConfig):
        self._decision_model = decision_model
        self._config = config

    def run(self, pair: str, bars: Sequence[Bar]) -> ForexBacktestResult:
        equity = self._config.starting_equity
        equity_curve: list[float] = []
        trades: list[SimulatedForexTrade] = []
        open_position: _OpenForexPosition | None = None

        for i in range(self._config.warmup_bars, len(bars)):
            current_bar = bars[i]

            if open_position is not None:
                equity, open_position, closed_trade = self._process_open_position(pair, open_position, current_bar, equity)
                if closed_trade is not None:
                    trades.append(closed_trade)
                    equity_curve.append(equity)
                continue

            open_position = self._maybe_enter(pair, bars[: i + 1], current_bar, equity)

        if open_position is not None:
            equity, trade = self._force_close(pair, open_position, bars[-1], equity)
            trades.append(trade)
            equity_curve.append(equity)

        return ForexBacktestResult(
            pair=pair,
            trades=trades,
            equity_curve=equity_curve,
            starting_equity=self._config.starting_equity,
            ending_equity=equity,
        )

    def _maybe_enter(self, pair: str, window: Sequence[Bar], current_bar: Bar, equity: float) -> _OpenForexPosition | None:
        if len(window) < self._config.min_candles_for_signal:
            return None

        atr_values = atr(window, self._config.atr_period)
        latest_atr = atr_values[-1]
        if latest_atr != latest_atr or latest_atr <= 0:  # NaN (warming up) or degenerate
            return None

        scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(pair, window)) is not None]
        signal = self._decision_model.score(pair, window, scan_hits, self._config.confidence_threshold)
        if not signal.meets_threshold or signal.direction is TradeDirection.NEUTRAL:
            return None

        stop_distance = latest_atr * self._config.stop_atr_multiplier
        entry_price = current_bar.close

        if signal.direction is TradeDirection.BULLISH:
            stop_price = entry_price - stop_distance
            take_profit_price = entry_price + stop_distance * self._config.take_profit_r_multiple
        else:
            stop_price = entry_price + stop_distance
            take_profit_price = entry_price - stop_distance * self._config.take_profit_r_multiple

        return _OpenForexPosition(
            direction=signal.direction,
            entry_time=current_bar.timestamp,
            entry_price=entry_price,
            stop_price=stop_price,
            stop_distance=stop_distance,
            take_profit_price=take_profit_price,
            risk_dollars=equity * self._config.risk_pct_per_trade,
        )

    def _process_open_position(self, pair: str, position: _OpenForexPosition, bar: Bar, equity: float):
        if position.direction is TradeDirection.BULLISH:
            if bar.low <= position.stop_price:
                return self._close(pair, position, position.stop_price, bar.timestamp, "stop_loss", equity)
            if bar.high >= position.take_profit_price:
                return self._close(pair, position, position.take_profit_price, bar.timestamp, "take_profit", equity)
            candidate_stop = bar.high - position.stop_distance
            if candidate_stop > position.stop_price:
                position = replace(position, stop_price=candidate_stop)
        else:
            if bar.high >= position.stop_price:
                return self._close(pair, position, position.stop_price, bar.timestamp, "stop_loss", equity)
            if bar.low <= position.take_profit_price:
                return self._close(pair, position, position.take_profit_price, bar.timestamp, "take_profit", equity)
            candidate_stop = bar.low + position.stop_distance
            if candidate_stop < position.stop_price:
                position = replace(position, stop_price=candidate_stop)

        return equity, position, None

    def _close(self, pair, position: _OpenForexPosition, exit_price: float, exit_time: datetime, reason: str, equity: float):
        price_move = exit_price - position.entry_price
        if position.direction is TradeDirection.BEARISH:
            price_move = -price_move
        r_multiple = price_move / position.stop_distance
        pnl = r_multiple * position.risk_dollars
        trade = SimulatedForexTrade(
            pair=pair,
            direction=position.direction,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=exit_price,
            r_multiple=r_multiple,
            pnl=pnl,
            exit_reason=reason,
        )
        return equity + pnl, None, trade

    def _force_close(self, pair: str, position: _OpenForexPosition, last_bar: Bar, equity: float):
        equity, _, trade = self._close(pair, position, last_bar.close, last_bar.timestamp, "end_of_data", equity)
        return equity, trade
