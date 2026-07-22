import random

import pytest
from bt_factories import make_bars

from decision_engine.models import TradeDirection
from decision_engine.scoring import WeightedFactorModel
from options.models import StrategyType
from risk.kelly import KellySizer
from trade_management.models import TradeManagementConfig
from backtesting.engine import BacktestEngine
from backtesting.models import BacktestConfig

# A momentum-only model makes confidence a deterministic function of RSI,
# isolating engine orchestration (entry/exit/sizing/equity tracking) from
# decision_engine's exact weighting formula, which is tested separately.
MOMENTUM_ONLY_WEIGHTS = {"momentum": 1.0, "trend": 0.0, "macd": 0.0, "unusual_volume": 0.0, "gap": 0.0}


def make_engine(confidence_threshold=90, target_dte=60, fallback_fraction=0.1, **tm_overrides):
    model = WeightedFactorModel(weights=MOMENTUM_ONLY_WEIGHTS)
    kelly = KellySizer(fallback_fraction=fallback_fraction, min_sample_size=100)
    tm_defaults = dict(
        stop_loss_pct=0.50, profit_target_pct=1.00, scale_out_fraction=0.50, trailing_stop_pct=0.20,
        min_trading_days_before_expiry=2,
    )
    tm_defaults.update(tm_overrides)
    tm_config = TradeManagementConfig(**tm_defaults)
    config = BacktestConfig(
        starting_equity=10000, confidence_threshold=confidence_threshold, target_delta=0.4,
        target_dte=target_dte, volatility_lookback=20, warmup_bars=40,
    )
    return BacktestEngine(model, kelly, tm_config, config)


def rising_closes_with_noise(seed=4, up_days=40):
    # Mostly-up days with occasional small down days: keeps RSI high enough
    # to clear a 90 confidence threshold while giving realized_volatility a
    # non-degenerate baseline (a perfectly smooth rise has ~0 volatility,
    # which makes the very next bar's move look like an extreme outlier and
    # spikes the vol proxy pathologically — see volatility_estimator.py).
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(up_days):
        step = rng.choice([1.0, 1.0, 1.0, 1.0, -0.3])
        closes.append(closes[-1] + step)
    return closes, rng


def test_no_entry_when_confidence_never_clears_threshold():
    engine = make_engine()
    bars = make_bars([100.0] * 80, spread=0.3)

    result = engine.run("TEST", bars)

    assert result.trades == []
    assert result.equity_curve == []
    assert result.ending_equity == result.starting_equity


def test_stop_loss_closes_full_position_at_a_loss():
    engine = make_engine()
    closes, rng = rising_closes_with_noise()
    for _ in range(15):
        closes.append(closes[-1] + (-1.5 + rng.uniform(-0.4, 0.4)))
    bars = make_bars(closes, spread=0.3)

    result = engine.run("TEST", bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.strategy_type is StrategyType.LONG_CALL
    assert trade.direction is TradeDirection.BULLISH
    assert trade.pnl < 0
    # Stop-loss fires once unrealized loss reaches -50% — checked once per
    # bar, not continuously, so it can overshoot before being detected.
    # Bound it loosely: past the trigger, but not a total wipeout.
    loss_pct = (trade.exit_value_per_unit - trade.entry_cost_per_unit) / trade.entry_cost_per_unit
    assert -0.90 <= loss_pct <= -0.50
    assert result.ending_equity == pytest.approx(result.starting_equity + trade.pnl)


def test_scale_out_then_trailing_stop_on_remainder():
    engine = make_engine()
    closes, rng = rising_closes_with_noise()
    closes += [closes[-1] + 1.0, closes[-1] + 2.0]  # push over +100% -> scale_out
    for _ in range(10):
        closes.append(closes[-1] - 1.2)  # pull back -> trailing_stop on the rest
    bars = make_bars(closes, spread=0.3)

    result = engine.run("TEST", bars)

    assert len(result.trades) == 2
    scale_out, trailing_stop = result.trades
    assert scale_out.exit_reason == "scale_out"
    assert trailing_stop.exit_reason == "trailing_stop"
    assert scale_out.entry_date == trailing_stop.entry_date  # same original position
    assert scale_out.pnl > 0
    assert scale_out.qty + trailing_stop.qty == 9  # original position fully accounted for
    total_pnl = scale_out.pnl + trailing_stop.pnl
    assert result.ending_equity == pytest.approx(result.starting_equity + total_pnl)


def test_expiry_exit_force_closes_regardless_of_pnl():
    engine = make_engine(target_dte=5)  # short expiration forces an early exit
    closes, rng = rising_closes_with_noise()
    for _ in range(10):
        closes.append(closes[-1] + rng.uniform(-0.2, 0.2))  # flat-ish: no stop/target trigger
    bars = make_bars(closes, spread=0.3)

    result = engine.run("TEST", bars)

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "expiry_exit"


def test_open_position_at_end_of_data_is_force_closed_and_marked():
    engine = make_engine()
    closes, _ = rising_closes_with_noise()
    closes.append(closes[-1] + 1.0)  # one more bar, not enough to hit any exit rule
    bars = make_bars(closes, spread=0.3)

    result = engine.run("TEST", bars)

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "end_of_data"
    assert result.trades[0].exit_date == bars[-1].timestamp.date()


def test_position_sizing_uses_fallback_kelly_fraction_of_equity():
    engine = make_engine(fallback_fraction=0.1)
    closes, rng = rising_closes_with_noise()
    for _ in range(15):
        closes.append(closes[-1] + (-1.5 + rng.uniform(-0.4, 0.4)))
    bars = make_bars(closes, spread=0.3)

    result = engine.run("TEST", bars)

    trade = result.trades[0]
    expected_qty = int((10000 * 0.1) // trade.entry_cost_per_unit)
    assert trade.qty == expected_qty


def test_config_rejects_invalid_values():
    with pytest.raises(ValueError):
        BacktestConfig(starting_equity=0, confidence_threshold=90, target_delta=0.4, target_dte=30)
    with pytest.raises(ValueError):
        BacktestConfig(starting_equity=1000, confidence_threshold=150, target_delta=0.4, target_dte=30)
    with pytest.raises(ValueError):
        BacktestConfig(starting_equity=1000, confidence_threshold=90, target_delta=0.0, target_dte=30)
    with pytest.raises(ValueError):
        BacktestConfig(starting_equity=1000, confidence_threshold=90, target_delta=0.4, target_dte=0)
