from datetime import date, timedelta

import pytest

from backtesting.walk_forward import split_walk_forward_windows


def test_splits_non_overlapping_windows_by_default():
    windows = split_walk_forward_windows(date(2026, 1, 1), date(2026, 4, 1), train_days=30, test_days=15)

    assert len(windows) > 0
    first = windows[0]
    assert first.train_start == date(2026, 1, 1)
    assert first.train_end == date(2026, 1, 31)
    assert first.test_start == date(2026, 1, 31)
    assert first.test_end == date(2026, 2, 15)

    if len(windows) > 1:
        # step defaults to test_days: TEST periods are contiguous/non-overlapping,
        # even though TRAIN periods (longer than the step) naturally overlap.
        assert windows[1].test_start == first.test_end


def test_windows_never_exceed_end_date():
    end = date(2026, 4, 1)
    windows = split_walk_forward_windows(date(2026, 1, 1), end, train_days=30, test_days=15)
    assert all(w.test_end <= end for w in windows)


def test_custom_step_days_creates_overlapping_windows():
    windows = split_walk_forward_windows(date(2026, 1, 1), date(2026, 3, 1), train_days=20, test_days=10, step_days=5)
    assert len(windows) >= 2
    assert windows[1].train_start == windows[0].train_start + timedelta(days=5)
    assert windows[1].train_start < windows[0].test_end  # overlaps with window 0's test period


def test_rejects_non_positive_train_or_test_days():
    with pytest.raises(ValueError):
        split_walk_forward_windows(date(2026, 1, 1), date(2026, 4, 1), train_days=0, test_days=15)
    with pytest.raises(ValueError):
        split_walk_forward_windows(date(2026, 1, 1), date(2026, 4, 1), train_days=30, test_days=0)


def test_returns_empty_list_when_range_too_short():
    windows = split_walk_forward_windows(date(2026, 1, 1), date(2026, 1, 10), train_days=30, test_days=15)
    assert windows == []
