from datetime import datetime, timedelta, timezone

import pytest
from bt_factories import make_hourly_bars

from broker.models import Bar
from decision_engine.models import TradeDirection
from decision_engine.scoring import WeightedFactorModel

from backtesting.forex_engine import ForexBacktestEngine
from backtesting.forex_models import ForexBacktestConfig

# Isolates engine orchestration (entry/exit/sizing/equity tracking) from
# decision_engine's exact weighting formula, same technique test_engine.py
# uses for the options engine -- momentum (RSI-based) is the only live factor.
MOMENTUM_ONLY_WEIGHTS = {
    "momentum": 1.0, "trend": 0.0, "macd": 0.0, "unusual_volume": 0.0,
    "gap": 0.0, "candlestick": 0.0, "congress": 0.0,
}

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_engine(confidence_threshold=90, risk_pct=0.02, stop_atr_multiplier=2.0, take_profit_r_multiple=1.0, warmup_bars=40):
    model = WeightedFactorModel(weights=MOMENTUM_ONLY_WEIGHTS)
    config = ForexBacktestConfig(
        starting_equity=10000,
        confidence_threshold=confidence_threshold,
        risk_pct_per_trade=risk_pct,
        stop_atr_multiplier=stop_atr_multiplier,
        take_profit_r_multiple=take_profit_r_multiple,
        atr_period=14,
        min_candles_for_signal=30,
        warmup_bars=warmup_bars,
    )
    return ForexBacktestEngine(model, config)


def _bar(index, close, spread=0.0005, low=None, high=None):
    return Bar(
        symbol="EUR_USD",
        timestamp=_START + timedelta(hours=index),
        open=close,
        high=high if high is not None else close + spread,
        low=low if low is not None else close - spread,
        close=close,
        volume=100.0,
    )


def _rising_bars(n, start_close=1.1000, step=0.0010, spread=0.0005):
    # Purely monotonic (no noise) so RSI saturates predictably and entry
    # timing is deterministic across test runs.
    return [_bar(i, start_close + step * i, spread=spread) for i in range(n)]


def test_no_entry_when_confidence_never_clears_threshold():
    engine = make_engine()
    bars = make_hourly_bars([1.1000] * 80, spread=0.0005)

    result = engine.run("EUR_USD", bars)

    assert result.trades == []
    assert result.equity_curve == []
    assert result.ending_equity == result.starting_equity


def test_stop_loss_exit_closes_at_stop_price():
    engine = make_engine()
    # 41 bars (indices 0-40) of a pure uptrend -- warmup_bars=40 means the
    # first evaluated bar is index 40, so entry (if it triggers) happens
    # exactly there, deterministically.
    bars = _rising_bars(41)
    # A catastrophic bar right after entry -- low is far below any plausible
    # ATR-derived stop regardless of the exact stop_distance computed, so
    # this test doesn't need to hand-compute ATR to force the hit.
    crash_close = bars[-1].close - 0.01
    bars.append(_bar(41, crash_close, low=bars[-1].close - 1.0, high=crash_close + 0.0005))

    result = engine.run("EUR_USD", bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction is TradeDirection.BULLISH
    assert trade.exit_reason == "stop_loss"
    assert trade.r_multiple == pytest.approx(-1.0, abs=0.01)  # stop hit == exactly -1R by construction
    assert trade.pnl < 0
    assert result.ending_equity == pytest.approx(result.starting_equity + trade.pnl)


def test_take_profit_exit_closes_at_target_price():
    engine = make_engine(take_profit_r_multiple=1.0)
    bars = _rising_bars(41)
    # A spike bar right after entry -- high is far above any plausible target.
    spike_close = bars[-1].close + 0.01
    bars.append(_bar(41, spike_close, low=spike_close - 0.0005, high=bars[-1].close + 1.0))

    result = engine.run("EUR_USD", bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.r_multiple == pytest.approx(1.0, abs=0.01)  # target hit == exactly +1R by construction
    assert trade.pnl > 0
    assert result.ending_equity == pytest.approx(result.starting_equity + trade.pnl)


def test_trailing_stop_ratchets_favorably_before_reversing():
    engine = make_engine(take_profit_r_multiple=100.0)  # effectively unreachable, isolates the trailing stop
    bars = _rising_bars(41)
    entry_price = bars[-1].close

    # Several bars that keep pushing higher (ratcheting the stop up) before
    # a bar low enough to hit the ratcheted stop but NOT low enough to have
    # hit the ORIGINAL entry-time stop -- proves the stop actually moved.
    running_high = entry_price
    for i in range(41, 46):
        running_high += 0.0020
        bars.append(_bar(i, running_high, spread=0.0003))
    # This bar's low is below the most recent high but well above the
    # original entry price -- only a ratcheted stop would catch it.
    reversal_low = entry_price + 0.0030
    bars.append(_bar(46, running_high, low=reversal_low, high=running_high + 0.0003))

    result = engine.run("EUR_USD", bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    # The stop caught the pullback well above the original entry price --
    # proof it ratcheted forward rather than staying at its initial level.
    assert trade.exit_price > entry_price


def test_force_closes_open_position_at_end_of_data():
    engine = make_engine(take_profit_r_multiple=100.0)
    bars = _rising_bars(41)
    # A few more mild bars that never touch stop or (effectively unreachable) target.
    bars += [_bar(41 + i, bars[-1].close + 0.0002 * i, spread=0.0003) for i in range(5)]

    result = engine.run("EUR_USD", bars)

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "end_of_data"
    assert result.trades[0].exit_price == bars[-1].close
