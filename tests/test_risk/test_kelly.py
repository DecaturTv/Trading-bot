import pytest

from risk.kelly import KellySizer, TradeStatistics


def test_full_kelly_matches_hand_calculation():
    # f* = win_rate - (1 - win_rate) / (avg_win / avg_loss) = 0.55 - 0.45/1.5 = 0.25
    stats = TradeStatistics(win_rate=0.55, avg_win=150.0, avg_loss=100.0, sample_size=100)
    sizer = KellySizer(kelly_fraction=0.25, min_sample_size=30)

    result = sizer.size(stats)

    assert result.full_kelly_fraction == pytest.approx(0.25)
    assert result.position_fraction == pytest.approx(0.0625)
    assert result.used_fallback is False


def test_fair_coin_even_payoff_has_zero_edge():
    stats = TradeStatistics(win_rate=0.5, avg_win=100.0, avg_loss=100.0, sample_size=100)
    sizer = KellySizer(min_sample_size=30)

    result = sizer.size(stats)

    assert result.full_kelly_fraction == pytest.approx(0.0)
    assert result.position_fraction == pytest.approx(0.0)


def test_negative_edge_clips_to_zero_not_negative():
    stats = TradeStatistics(win_rate=0.4, avg_win=100.0, avg_loss=100.0, sample_size=100)
    sizer = KellySizer(min_sample_size=30)

    result = sizer.size(stats)

    assert result.full_kelly_fraction == 0.0
    assert result.position_fraction == 0.0


def test_falls_back_when_sample_size_too_small():
    stats = TradeStatistics(win_rate=0.9, avg_win=1000.0, avg_loss=1.0, sample_size=5)
    sizer = KellySizer(min_sample_size=30, fallback_fraction=0.02)

    result = sizer.size(stats)

    assert result.used_fallback is True
    assert result.position_fraction == pytest.approx(0.02)
    assert result.full_kelly_fraction == 0.0


def test_falls_back_when_stats_is_none():
    sizer = KellySizer(fallback_fraction=0.02)
    result = sizer.size(None)

    assert result.used_fallback is True
    assert result.position_fraction == pytest.approx(0.02)


def test_max_position_fraction_caps_aggressive_kelly():
    stats = TradeStatistics(win_rate=0.99, avg_win=100.0, avg_loss=1.0, sample_size=100)
    sizer = KellySizer(kelly_fraction=1.0, min_sample_size=30, max_position_fraction=0.5)

    result = sizer.size(stats)

    assert result.full_kelly_fraction > 0.5
    assert result.position_fraction == pytest.approx(0.5)


def test_rejects_invalid_win_rate():
    sizer = KellySizer(min_sample_size=1)
    with pytest.raises(ValueError):
        sizer.size(TradeStatistics(win_rate=1.5, avg_win=100.0, avg_loss=100.0, sample_size=50))


def test_rejects_non_positive_avg_loss():
    sizer = KellySizer(min_sample_size=1)
    with pytest.raises(ValueError):
        sizer.size(TradeStatistics(win_rate=0.5, avg_win=100.0, avg_loss=0.0, sample_size=50))


def test_rejects_non_positive_avg_win():
    sizer = KellySizer(min_sample_size=1)
    with pytest.raises(ValueError):
        sizer.size(TradeStatistics(win_rate=0.5, avg_win=0.0, avg_loss=100.0, sample_size=50))


def test_constructor_rejects_invalid_kelly_fraction():
    with pytest.raises(ValueError):
        KellySizer(kelly_fraction=0.0)
    with pytest.raises(ValueError):
        KellySizer(kelly_fraction=1.5)


def test_constructor_rejects_invalid_fallback_fraction():
    with pytest.raises(ValueError):
        KellySizer(fallback_fraction=-0.1)
    with pytest.raises(ValueError):
        KellySizer(fallback_fraction=1.5)
