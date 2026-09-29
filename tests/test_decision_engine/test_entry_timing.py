from datetime import datetime, timedelta, timezone

from broker.models import Bar
from decision_engine.entry_timing import chase_rejection, drop_forming_bar
from decision_engine.models import TradeDirection

NOW = datetime(2026, 9, 29, 15, 2, tzinfo=timezone.utc)


def five_min_bars(closes, spread=0.5):
    """Completed 5Min bars ending just before NOW, plus nothing forming."""
    start = NOW.replace(minute=0) - timedelta(minutes=5 * len(closes))
    return [
        Bar(symbol="T", timestamp=start + timedelta(minutes=5 * i), open=c, high=c + spread, low=c - spread, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


def forming(price):
    return Bar(symbol="T", timestamp=NOW.replace(minute=0), open=price, high=price, low=price, close=price, volume=1.0)


def test_drop_forming_bar_removes_only_the_open_bar():
    bars = five_min_bars([100.0] * 3)
    assert drop_forming_bar(bars + [forming(101.0)], "5Min", NOW) == bars
    assert drop_forming_bar(bars, "5Min", NOW) == bars


def test_drop_forming_bar_daily_bar_is_forming_all_session():
    today = Bar(symbol="T", timestamp=datetime(2026, 9, 29, 4, tzinfo=timezone.utc), open=1, high=1, low=1, close=1, volume=1)
    assert drop_forming_bar([today], "1Day", NOW) == []


def test_flat_market_is_not_chasing():
    bars = five_min_bars([100.0, 100.2] * 10) + [forming(100.1)]
    assert chase_rejection(bars, TradeDirection.BULLISH, NOW, 2.0, 0.75) is None


def test_bought_after_a_big_run_is_chasing():
    bars = five_min_bars([100.0] * 14 + [100.0 + i for i in range(1, 13)]) + [forming(113.0)]
    reason = chase_rejection(bars, TradeDirection.BULLISH, NOW, 2.0, 0.75)
    assert reason is not None and "ATR" in reason


def test_put_bought_at_the_low_is_chasing_but_call_is_not():
    bars = five_min_bars([100.0, 101.0] * 7 + [100.5, 99.8, 100.4, 99.9] * 3) + [forming(99.4)]
    assert "range" in chase_rejection(bars, TradeDirection.BEARISH, NOW, 99.0, 0.75)
    assert chase_rejection(bars, TradeDirection.BULLISH, NOW, 99.0, 0.75) is None


def test_not_enough_bars_allows_entry():
    assert chase_rejection(five_min_bars([100.0, 150.0]), TradeDirection.BULLISH, NOW, 2.0, 0.75) is None


def test_underlying_stop_level_below_for_calls_above_for_puts():
    from decision_engine.entry_timing import underlying_stop_level

    bars = five_min_bars([100.0] * 20, spread=0.5) + [forming(100.0)]  # ATR = 1.0
    assert underlying_stop_level(bars, TradeDirection.BULLISH, NOW, 3.0) == (100.0, 97.0)
    assert underlying_stop_level(bars, TradeDirection.BEARISH, NOW, 3.0) == (100.0, 103.0)
    assert underlying_stop_level(bars[:5], TradeDirection.BULLISH, NOW, 3.0) is None
