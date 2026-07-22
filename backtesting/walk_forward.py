from dataclasses import dataclass
from datetime import date, timedelta

from broker.models import Bar

from .engine import BacktestEngine
from .models import BacktestResult


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def split_walk_forward_windows(
    start: date, end: date, train_days: int, test_days: int, step_days: int | None = None
) -> list[WalkForwardWindow]:
    """Rolling [train_start, train_end) then [test_start, test_end) windows,
    stepping forward by step_days (defaults to test_days: non-overlapping
    test periods)."""
    if train_days <= 0 or test_days <= 0:
        raise ValueError("train_days and test_days must be positive")
    step = step_days if step_days is not None else test_days
    if step <= 0:
        raise ValueError("step_days must be positive")

    windows = []
    cursor = start
    while True:
        train_end = cursor + timedelta(days=train_days)
        test_end = train_end + timedelta(days=test_days)
        if test_end > end:
            break
        windows.append(WalkForwardWindow(train_start=cursor, train_end=train_end, test_start=train_end, test_end=test_end))
        cursor += timedelta(days=step)
    return windows


def run_walk_forward(
    engine: BacktestEngine, symbol: str, bars: list[Bar], windows: list[WalkForwardWindow]
) -> list[BacktestResult]:
    """Runs engine unchanged over each window's test period.

    This project has no automated parameter search/optimizer yet, so train
    windows aren't used to calibrate anything here — this validates that a
    FIXED strategy performs consistently across rolling out-of-sample
    periods, not parameter stability under re-optimization. Wiring train-
    window calibration in is a natural extension once ml/ exists.
    """
    results = []
    for window in windows:
        window_bars = [b for b in bars if window.test_start <= b.timestamp.date() < window.test_end]
        if not window_bars:
            continue
        results.append(engine.run(symbol, window_bars))
    return results
