from datetime import datetime, timedelta, timezone

from broker.models import Bar
from indicators.candlesticks import detect_doji, detect_engulfing, detect_hammer_family, detect_pattern, detect_star


def make_bar(open, high, low, close, i=0):
    return Bar(
        symbol="TEST", timestamp=datetime.now(timezone.utc) + timedelta(minutes=i),
        open=open, high=high, low=low, close=close, volume=1000.0,
    )


def flat_run(n, price=50.0, step=0.0):
    """n plain small-range bars, optionally drifting by `step` each bar, to
    seed a local trend before the pattern candle(s)."""
    return [make_bar(price + i * step, price + i * step + 0.2, price + i * step - 0.2, price + i * step, i) for i in range(n)]


def test_bullish_engulfing_detected():
    prev = make_bar(open=110, high=111, low=99, close=100, i=0)
    curr = make_bar(open=99, high=114, low=98, close=113, i=1)
    pattern = detect_engulfing([prev, curr])
    assert pattern is not None
    assert pattern.name == "bullish_engulfing"
    assert pattern.direction == 1.0


def test_bearish_engulfing_detected():
    prev = make_bar(open=100, high=111, low=99, close=110, i=0)
    curr = make_bar(open=111, high=112, low=96, close=97, i=1)
    pattern = detect_engulfing([prev, curr])
    assert pattern is not None
    assert pattern.name == "bearish_engulfing"
    assert pattern.direction == -1.0


def test_engulfing_none_when_no_relationship():
    prev = make_bar(open=100, high=101, low=99, close=100.5, i=0)
    curr = make_bar(open=100.5, high=101, low=100, close=100.8, i=1)
    assert detect_engulfing([prev, curr]) is None


def test_hammer_detected_in_downtrend():
    bars = flat_run(6, price=52, step=-0.5)  # downtrend into the hammer
    hammer = make_bar(open=50.2, high=50.6, low=48.0, close=50.5, i=6)
    pattern = detect_hammer_family(bars + [hammer])
    assert pattern is not None
    assert pattern.name == "hammer"
    assert pattern.direction == 1.0


def test_hanging_man_detected_in_uptrend():
    bars = flat_run(6, price=48, step=0.5)  # uptrend into the hanging man
    candle = make_bar(open=50.2, high=50.6, low=48.0, close=50.5, i=6)
    pattern = detect_hammer_family(bars + [candle])
    assert pattern is not None
    assert pattern.name == "hanging_man"
    assert pattern.direction == -1.0


def test_shooting_star_detected_in_uptrend():
    bars = flat_run(6, price=48, step=0.5)  # uptrend into the shooting star
    star = make_bar(open=50.5, high=53.0, low=50.4, close=50.2, i=6)
    pattern = detect_hammer_family(bars + [star])
    assert pattern is not None
    assert pattern.name == "shooting_star"
    assert pattern.direction == -1.0


def test_inverted_hammer_detected_in_downtrend():
    bars = flat_run(6, price=52, step=-0.5)  # downtrend into the inverted hammer
    star = make_bar(open=50.5, high=53.0, low=50.4, close=50.2, i=6)
    pattern = detect_hammer_family(bars + [star])
    assert pattern is not None
    assert pattern.name == "inverted_hammer"
    assert pattern.direction == 1.0


def test_hammer_family_none_when_body_too_large():
    bars = flat_run(6, price=52, step=-0.5)
    candle = make_bar(open=48.0, high=50.6, low=47.8, close=50.5, i=6)  # big body, no long wick advantage
    assert detect_hammer_family(bars + [candle]) is None


def test_doji_detected_signals_trend_exhaustion():
    bars = flat_run(6, price=48, step=0.5)  # uptrend
    doji = make_bar(open=100.0, high=102.0, low=98.0, close=100.05, i=6)
    pattern = detect_doji(bars + [doji])
    assert pattern is not None
    assert pattern.name == "doji"
    assert pattern.direction == -1.0  # exhaustion of the preceding uptrend


def test_doji_none_without_trend_context():
    flat = flat_run(6, price=50, step=0.0)
    doji = make_bar(open=100.0, high=102.0, low=98.0, close=100.05, i=6)
    assert detect_doji(flat + [doji]) is None


def test_morning_star_detected():
    first = make_bar(open=110, high=111, low=99, close=100, i=0)
    star = make_bar(open=98.8, high=99.0, low=98.5, close=98.7, i=1)
    third = make_bar(open=99, high=109, low=98, close=108, i=2)
    pattern = detect_star([first, star, third])
    assert pattern is not None
    assert pattern.name == "morning_star"
    assert pattern.direction == 1.0


def test_evening_star_detected():
    first = make_bar(open=100, high=111, low=99, close=110, i=0)
    star = make_bar(open=111.2, high=111.5, low=111.0, close=111.3, i=1)
    third = make_bar(open=111, high=112, low=101, close=102, i=2)
    pattern = detect_star([first, star, third])
    assert pattern is not None
    assert pattern.name == "evening_star"
    assert pattern.direction == -1.0


def test_detect_pattern_returns_none_when_nothing_matches():
    flat = flat_run(10, price=50, step=0.0)
    assert detect_pattern(flat) is None


def test_detect_pattern_prioritizes_star_over_weaker_patterns():
    first = make_bar(open=110, high=111, low=99, close=100, i=0)
    star = make_bar(open=98.8, high=99.0, low=98.5, close=98.7, i=1)
    third = make_bar(open=99, high=109, low=98, close=108, i=2)
    pattern = detect_pattern([first, star, third])
    assert pattern is not None
    assert pattern.name == "morning_star"
