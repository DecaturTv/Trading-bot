from datetime import date

import pytest

from decision_engine.models import TradeDirection
from options.models import StrategyType
from backtesting.models import SimulatedTrade
from backtesting.statistics import compute_trade_statistics


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


def test_returns_none_for_empty_trade_list():
    assert compute_trade_statistics([]) is None


def test_computes_win_rate_and_payoff_ratio():
    trades = [make_trade(150.0), make_trade(150.0), make_trade(-100.0)]
    stats = compute_trade_statistics(trades)

    assert stats is not None
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.avg_win == pytest.approx(150.0)
    assert stats.avg_loss == pytest.approx(100.0)
    assert stats.sample_size == 3


def test_returns_none_when_all_trades_are_wins():
    stats = compute_trade_statistics([make_trade(100.0), make_trade(50.0)])
    assert stats is None


def test_returns_none_when_all_trades_are_losses():
    stats = compute_trade_statistics([make_trade(-100.0), make_trade(-50.0)])
    assert stats is None
