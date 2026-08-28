import random
from datetime import datetime, timezone

import pytest
from bt_factories import make_bars, make_hourly_bars

from tournament.runner import (
    _ROC_CAP,
    _normalized_equity_pnl,
    _normalized_forex_pnl,
    _pooled_drawdown,
    _summarize,
    run_equities_strategy,
    run_forex_strategy,
)
from tournament.strategies import MOMENTUM_RIDER, STRATEGIES


def test_normalized_equity_pnl_scales_return_on_capital_to_a_fixed_notional():
    # A trade that returned +40% on the capital it deployed, re-scored against
    # a $500 notional -> +$200, regardless of how big the compounded raw
    # position actually was.
    assert _normalized_equity_pnl(entry_cost_per_unit=100.0, qty=7, raw_pnl=280.0, notional_per_trade=500.0) == pytest.approx(200.0)


def test_normalized_equity_pnl_is_zero_when_no_capital_deployed():
    assert _normalized_equity_pnl(entry_cost_per_unit=0.0, qty=5, raw_pnl=10.0, notional_per_trade=500.0) == 0.0


def test_normalized_equity_pnl_does_not_compound_across_a_huge_raw_pnl():
    # Same +40% return, but the raw position had compounded to an absurd size.
    # Normalization must still land at +$200, not millions.
    huge = _normalized_equity_pnl(entry_cost_per_unit=1e6, qty=1000, raw_pnl=4e8, notional_per_trade=500.0)
    assert huge == pytest.approx(200.0)


def test_normalized_equity_pnl_caps_a_synthetic_pricing_outlier():
    # A near-zero premium ($1e-9/contract) the engine "bought" a billion of and
    # that then repriced into the billions -> return on capital ~1e9. Without
    # the clamp this one trade contributes ~1e12; with it, at most _ROC_CAP
    # (10) x the notional.
    capped = _normalized_equity_pnl(entry_cost_per_unit=1e-9, qty=1_000_000_000, raw_pnl=1e12, notional_per_trade=500.0)
    assert capped == pytest.approx(10.0 * 500.0)


def test_normalized_equity_pnl_floors_a_total_loss_at_minus_one_notional():
    # Rounding in the synthetic value can push a wiped-out position slightly
    # past -100%; a long debit trade can't lose more than the notional it stood in for.
    floored = _normalized_equity_pnl(entry_cost_per_unit=100.0, qty=10, raw_pnl=-1500.0, notional_per_trade=500.0)
    assert floored == pytest.approx(-500.0)


def test_normalized_forex_pnl_is_r_multiple_times_notional():
    assert _normalized_forex_pnl(r_multiple=1.5, notional_per_trade=6.0) == pytest.approx(9.0)
    assert _normalized_forex_pnl(r_multiple=-1.0, notional_per_trade=6.0) == pytest.approx(-6.0)


def test_pooled_drawdown_is_peak_to_trough_fraction():
    # bankroll 100 -> 150 -> 90 : peak 150, trough 90 -> 40% drawdown
    assert _pooled_drawdown([50.0, -60.0, 10.0], bankroll=100.0) == pytest.approx(0.40)


def test_pooled_drawdown_zero_when_only_gains():
    assert _pooled_drawdown([5.0, 5.0, 5.0], bankroll=100.0) == 0.0


def test_summarize_orders_pnl_by_time_for_drawdown():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 3, tzinfo=timezone.utc)
    # Passed out of order; drawdown must still see +100 then -50 then +10.
    result = _summarize("X", [(t2, 10.0), (t0, 100.0), (t1, -50.0)], symbols_traded=2, bankroll=1000.0)
    assert result.total_pnl == pytest.approx(60.0)
    assert result.trade_count == 3
    assert result.wins == 2 and result.losses == 1
    assert result.win_rate == pytest.approx(2 / 3)
    assert result.ending_bankroll == pytest.approx(1060.0)
    # peak 1100 after the +100, trough 1050 after the -50 -> ~4.5%
    assert result.max_drawdown_pct == pytest.approx(50 / 1100)


def test_summarize_handles_zero_trades():
    result = _summarize("X", [], symbols_traded=0, bankroll=500.0)
    assert result.total_pnl == 0.0
    assert result.win_rate == 0.0
    assert result.max_drawdown_pct == 0.0
    assert result.ending_bankroll == 500.0


def _rising_then_falling_closes(seed=7):
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(90):
        closes.append(closes[-1] + rng.choice([1.0, 1.0, 1.0, -0.4]))
    for _ in range(30):
        closes.append(closes[-1] + rng.uniform(-1.6, 0.3))
    return closes


def test_run_equities_strategy_produces_a_result_over_synthetic_bars():
    closes = _rising_then_falling_closes()
    bars_by_symbol = {"AAA": make_bars(closes, spread=0.4), "BBB": make_bars(closes[::-1], spread=0.4)}

    result = run_equities_strategy(MOMENTUM_RIDER, bars_by_symbol, bankroll=2100.0)

    assert result.name == "Momentum Rider"
    assert result.trade_count >= 1
    assert result.ending_bankroll == pytest.approx(2100.0 + result.total_pnl)
    assert 0 <= result.win_rate <= 1
    # No trade may contribute more than _ROC_CAP x the per-trade notional
    # (2100 x 0.25), so the aggregate can't run away the way the raw
    # compounded engine P&L does.
    assert abs(result.total_pnl) <= result.trade_count * _ROC_CAP * (2100.0 * 0.25)


def test_run_equities_strategy_skips_symbols_with_too_little_history():
    bars_by_symbol = {"SHORT": make_bars([100.0] * 10, spread=0.4)}
    result = run_equities_strategy(MOMENTUM_RIDER, bars_by_symbol, bankroll=2100.0)
    assert result.trade_count == 0
    assert result.symbols_traded == 0


def test_run_forex_strategy_produces_a_result_over_synthetic_bars():
    closes = _rising_then_falling_closes()
    scaled = [1.10 + c / 5000 for c in closes]
    bars_by_pair = {"EUR_USD": make_hourly_bars(scaled * 2, pair="EUR_USD", spread=0.0004)}

    result = run_forex_strategy(STRATEGIES[0], bars_by_pair, bankroll=300.0)

    assert result.name == STRATEGIES[0].name
    assert result.ending_bankroll == pytest.approx(300.0 + result.total_pnl)
