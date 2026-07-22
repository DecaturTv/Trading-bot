import pytest

from risk.statistics import compute_trade_statistics


def test_returns_none_for_empty_pnls():
    assert compute_trade_statistics([]) is None


def test_computes_win_rate_and_payoff_ratio():
    stats = compute_trade_statistics([150.0, 150.0, -100.0])

    assert stats is not None
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.avg_win == pytest.approx(150.0)
    assert stats.avg_loss == pytest.approx(100.0)
    assert stats.sample_size == 3


def test_returns_none_when_all_wins():
    assert compute_trade_statistics([100.0, 50.0]) is None


def test_returns_none_when_all_losses():
    assert compute_trade_statistics([-100.0, -50.0]) is None
