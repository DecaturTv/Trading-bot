import pytest

from options.iv_rank import iv_percentile, iv_rank


def test_iv_rank_scales_between_min_and_max():
    historical = [0.10, 0.20, 0.30, 0.40, 0.50]
    assert iv_rank(0.30, historical) == pytest.approx(50.0)
    assert iv_rank(0.10, historical) == pytest.approx(0.0)
    assert iv_rank(0.50, historical) == pytest.approx(100.0)


def test_iv_rank_clamps_outside_historical_range():
    historical = [0.10, 0.50]
    assert iv_rank(0.05, historical) == 0.0
    assert iv_rank(0.90, historical) == 100.0


def test_iv_rank_flat_history_returns_neutral():
    assert iv_rank(0.30, [0.20, 0.20, 0.20]) == 50.0


def test_iv_rank_rejects_empty_history():
    with pytest.raises(ValueError):
        iv_rank(0.30, [])


def test_iv_percentile_counts_strictly_below():
    historical = [0.10, 0.20, 0.30, 0.40, 0.50]
    assert iv_percentile(0.35, historical) == pytest.approx(60.0)
    assert iv_percentile(0.05, historical) == pytest.approx(0.0)
    assert iv_percentile(0.55, historical) == pytest.approx(100.0)


def test_iv_percentile_rejects_empty_history():
    with pytest.raises(ValueError):
        iv_percentile(0.30, [])
