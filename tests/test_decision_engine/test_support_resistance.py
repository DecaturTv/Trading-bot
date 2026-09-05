from datetime import datetime, timedelta, timezone

import pytest

from broker.models import Bar
from decision_engine.models import TradeDirection
from decision_engine.support_resistance import (
    BACKTESTED_SR_CONFIG,
    SRLevelConfig,
    detect_levels,
    sr_signal,
)
from indicators.volatility import atr as atr_fn


def _bar(i, high, low, close, open_=None):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Bar(
        symbol="TEST", timestamp=now + timedelta(minutes=5 * i),
        open=open_ if open_ is not None else close, high=high, low=low, close=close, volume=1000.0,
    )


def make_config(**overrides):
    defaults = dict(
        pivot_k=1, level_lookback_bars=6, cluster_tol=0.01, min_touches=2, touch_tol=0.01,
        atr_period=1, stop_atr_mult=0.5, min_reward_r=1.0, fallback_target_r=2.0,
    )
    defaults.update(overrides)
    return SRLevelConfig(**defaults)


def _padded(bars, n=3):
    """sr_signal (unlike detect_levels) requires len(bars) >= min_bars_required
    as a warmup floor. Prepends n flat, far-away bars so the fixtures above
    clear that floor without disturbing the real pattern's indices/neighbors
    -- padding bars are identical to each other so they never register as a
    swing point themselves (tie on min/max), and their price range (~50) is
    far enough from the fixtures' (~90-110) that any spurious cluster from
    them can't interfere with the level this test actually cares about."""
    padding = [_bar(-n + i, high=50.0, low=49.0, close=49.5) for i in range(n)]
    return padding + bars


def _support_bounce_bars():
    """Two confirmed swing lows near 100 (support), then a bar that pierces
    and closes back above it -- no resistance in range, so target falls back
    to fallback_target_r."""
    return [
        _bar(0, high=105, low=103, close=104),
        _bar(1, high=104, low=100.0, close=102),  # swing low #1
        _bar(2, high=106, low=104, close=105),
        _bar(3, high=107, low=105, close=106),
        _bar(4, high=105, low=100.2, close=104),  # swing low #2, clusters with #1
        _bar(5, high=108, low=103, close=107),
        _bar(6, high=109, low=105, close=108),
        _bar(7, high=102, low=100.0, close=101.5),  # touches support, closes above it
    ]


def _resistance_rejection_bars():
    """Mirror of the support scenario: two confirmed swing highs near 100
    (resistance), then a bar that pierces and closes back below it."""
    return [
        _bar(0, high=97, low=95, close=96),
        _bar(1, high=100.0, low=96, close=98),  # swing high #1
        _bar(2, high=94, low=92, close=93),
        _bar(3, high=93, low=91, close=92),
        _bar(4, high=100.2, low=96, close=97),  # swing high #2, clusters with #1
        _bar(5, high=92, low=89, close=90),
        _bar(6, high=91, low=87, close=88),
        _bar(7, high=100.0, low=98, close=98.5),  # touches resistance, closes below it
    ]


class TestSRLevelConfig:
    def test_rejects_non_positive_fields(self):
        for field in (
            "pivot_k", "level_lookback_bars", "cluster_tol", "min_touches", "touch_tol",
            "atr_period", "stop_atr_mult", "min_reward_r", "fallback_target_r",
        ):
            with pytest.raises(ValueError):
                make_config(**{field: 0})

    def test_min_bars_required(self):
        config = make_config(level_lookback_bars=780, pivot_k=5, atr_period=14)
        assert config.min_bars_required == 780 + 2 * 5 + 14 + 2


class TestDetectLevels:
    def test_finds_clustered_support_with_enough_touches(self):
        bars = _support_bounce_bars()
        config = make_config()
        supports, resistances = detect_levels(bars, config)
        assert resistances == []
        assert len(supports) == 1
        assert supports[0].touches == 2
        assert supports[0].price == pytest.approx((100.0 + 100.2) / 2)

    def test_single_touch_level_is_dropped(self):
        bars = _support_bounce_bars()
        config = make_config(min_touches=3)
        supports, _ = detect_levels(bars, config)
        assert supports == []

    def test_too_few_bars_returns_no_levels(self):
        config = make_config(pivot_k=5)
        supports, resistances = detect_levels(_support_bounce_bars(), config)
        assert supports == [] and resistances == []


class TestSrSignal:
    def test_too_few_bars_returns_none(self):
        config = make_config(level_lookback_bars=780)
        assert sr_signal(_support_bounce_bars(), config) is None

    def test_bullish_bounce_off_support(self):
        bars = _padded(_support_bounce_bars())
        config = make_config()
        signal = sr_signal(bars, config)

        assert signal is not None
        assert signal.direction is TradeDirection.BULLISH
        assert signal.entry == pytest.approx(bars[-1].close)

        expected_atr = atr_fn(bars, config.atr_period)[-1]
        expected_level = (100.0 + 100.2) / 2
        expected_stop = expected_level - config.stop_atr_mult * expected_atr
        assert signal.stop == pytest.approx(expected_stop)

        risk = signal.entry - signal.stop
        assert signal.target == pytest.approx(signal.entry + config.fallback_target_r * risk)
        assert signal.reward_r == pytest.approx(config.fallback_target_r)

    def test_uses_real_resistance_level_as_target_over_fallback(self):
        bars = _padded([
            _bar(0, high=105, low=103, close=104),
            _bar(1, high=104, low=100.0, close=102),   # swing low #1 (support)
            _bar(2, high=110.0, low=104, close=108),   # swing high #1 (resistance)
            _bar(3, high=107, low=105, close=106),
            _bar(4, high=105, low=100.2, close=104),   # swing low #2 (support cluster)
            _bar(5, high=110.2, low=103, close=107),   # swing high #2 (resistance cluster)
            _bar(6, high=109, low=105, close=108),
            _bar(7, high=102, low=100.0, close=101.5),  # touches support
        ])
        config = make_config()
        signal = sr_signal(bars, config)

        assert signal is not None
        assert signal.direction is TradeDirection.BULLISH
        assert signal.target == pytest.approx((110.0 + 110.2) / 2)
        risk = signal.entry - signal.stop
        assert signal.reward_r == pytest.approx((signal.target - signal.entry) / risk)
        assert signal.reward_r != pytest.approx(config.fallback_target_r)

    def test_bearish_rejection_at_resistance(self):
        bars = _padded(_resistance_rejection_bars())
        config = make_config()
        signal = sr_signal(bars, config)

        assert signal is not None
        assert signal.direction is TradeDirection.BEARISH
        assert signal.entry == pytest.approx(bars[-1].close)
        assert signal.stop > signal.entry
        assert signal.target < signal.entry
        assert signal.reward_r == pytest.approx(config.fallback_target_r)

    def test_returns_none_when_reward_below_min_r(self):
        bars = _padded(_support_bounce_bars())
        config = make_config(fallback_target_r=2.0, min_reward_r=3.0)
        assert sr_signal(bars, config) is None

    def test_returns_none_when_price_does_not_touch_a_level(self):
        bars = _support_bounce_bars()
        # Replace the last bar with one nowhere near the support level.
        bars = _padded(bars[:-1] + [_bar(7, high=150, low=140, close=145)])
        config = make_config()
        assert sr_signal(bars, config) is None


class TestBacktestedConfig:
    def test_is_valid(self):
        assert BACKTESTED_SR_CONFIG.pivot_k == 5
        assert BACKTESTED_SR_CONFIG.level_lookback_bars == 780
        assert BACKTESTED_SR_CONFIG.min_bars_required == 780 + 10 + 14 + 2
