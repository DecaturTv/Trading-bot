import random
from datetime import date

import pytest

from decision_engine.models import TradeDirection
from options.models import StrategyType
from backtesting.models import SimulatedTrade
from backtesting.monte_carlo import run_monte_carlo


def make_trade(pnl):
    return SimulatedTrade(
        symbol="TEST",
        strategy_type=StrategyType.LONG_CALL,
        direction=TradeDirection.BULLISH,
        entry_date=date(2026, 1, 1),
        exit_date=date(2026, 1, 5),
        entry_cost_per_unit=500.0,
        exit_value_per_unit=500.0 + pnl,
        qty=1,
        exit_reason="stop_loss",
        pnl=pnl,
    )


def test_result_has_one_outcome_per_simulation():
    trades = [make_trade(100.0), make_trade(-50.0)]
    result = run_monte_carlo(trades, starting_equity=1000.0, num_simulations=200, rng=random.Random(1))

    assert len(result.final_equities) == 200
    assert len(result.max_drawdowns) == 200


def test_deterministic_with_seeded_rng():
    trades = [make_trade(100.0), make_trade(-50.0), make_trade(30.0)]
    result_a = run_monte_carlo(trades, starting_equity=1000.0, num_simulations=50, rng=random.Random(42))
    result_b = run_monte_carlo(trades, starting_equity=1000.0, num_simulations=50, rng=random.Random(42))

    assert result_a.final_equities == result_b.final_equities
    assert result_a.max_drawdowns == result_b.max_drawdowns


def test_all_winning_trades_never_draws_down():
    trades = [make_trade(100.0), make_trade(50.0), make_trade(25.0)]
    result = run_monte_carlo(trades, starting_equity=1000.0, num_simulations=100, rng=random.Random(7))

    assert all(dd == 0.0 for dd in result.max_drawdowns)
    assert all(eq > 1000.0 for eq in result.final_equities)


def test_final_equity_mean_converges_to_expected_value():
    # Bootstrap resampling is WITH replacement, so any single simulation's
    # total can differ from the original trade sequence's total — but the
    # mean across many simulations should converge to
    # starting_equity + len(trades) * mean(pnl), by linearity of expectation.
    trades = [make_trade(100.0), make_trade(-200.0), make_trade(50.0)]
    result = run_monte_carlo(trades, starting_equity=1000.0, num_simulations=5000, rng=random.Random(3))

    mean_pnl = sum(t.pnl for t in trades) / len(trades)
    expected_mean_equity = 1000.0 + len(trades) * mean_pnl
    observed_mean_equity = sum(result.final_equities) / len(result.final_equities)

    assert observed_mean_equity == pytest.approx(expected_mean_equity, abs=15.0)


def test_rejects_empty_trades():
    with pytest.raises(ValueError):
        run_monte_carlo([], starting_equity=1000.0)


def test_rejects_non_positive_starting_equity():
    with pytest.raises(ValueError):
        run_monte_carlo([make_trade(10.0)], starting_equity=0.0)
