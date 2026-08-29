from datetime import datetime, timedelta, timezone

from decision_engine.models import TradeDirection
from options.models import StrategyType
from risk.kelly import KellySizer

from backtesting.models import SimulatedTrade
from backtesting.portfolio_sim import (
    CandidatePosition,
    PortfolioConfig,
    PositionExit,
    positions_from_trades,
    simulate_portfolio,
)

T0 = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)


def _leg(pid, symbol, entry_ts, exit_ts, qty, entry_cost, exit_val, priced="historical"):
    return SimulatedTrade(
        symbol=symbol, strategy_type=StrategyType.LONG_CALL, direction=TradeDirection.BULLISH,
        entry_date=entry_ts.date(), exit_date=exit_ts.date(), entry_cost_per_unit=entry_cost,
        exit_value_per_unit=exit_val, qty=qty, exit_reason="max_hold_exit", pnl=qty * (exit_val - entry_cost),
        priced_from=priced, entry_ts=entry_ts, exit_ts=exit_ts, position_id=pid,
    )


def _pos(symbol, entry_ts, entry_cost, exits):
    return CandidatePosition(
        symbol=symbol, strategy_type=StrategyType.LONG_CALL, direction=TradeDirection.BULLISH,
        entry_ts=entry_ts, entry_cost_per_unit=entry_cost, exits=exits,
    )


def _full_size_config(equity=10_000.0, max_concurrent=12):
    # kelly_fraction=1 + fallback=1 + cap=1 => each open uses all available cash
    kelly = KellySizer(kelly_fraction=1.0, min_sample_size=1, fallback_fraction=1.0, max_position_fraction=1.0)
    return PortfolioConfig(starting_equity=equity, kelly_sizer=kelly, max_concurrent_positions=max_concurrent)


# --- positions_from_trades -------------------------------------------------

def test_groups_scale_out_legs_into_one_position_with_fractions():
    legs = [
        _leg(1, "AAA", T0, T0 + timedelta(hours=1), qty=4, entry_cost=100, exit_val=130),
        _leg(1, "AAA", T0, T0 + timedelta(hours=3), qty=6, entry_cost=100, exit_val=150),
    ]
    positions = positions_from_trades(legs)
    assert len(positions) == 1
    p = positions[0]
    assert p.entry_cost_per_unit == 100
    assert [round(e.fraction, 2) for e in p.exits] == [0.4, 0.6]
    assert [e.is_last for e in p.exits] == [False, True]


def test_drops_legs_without_timestamps():
    t = _leg(1, "AAA", T0, T0 + timedelta(hours=1), 1, 100, 120)
    t = SimulatedTrade(**{**t.__dict__, "entry_ts": None})
    assert positions_from_trades([t]) == []


def test_same_position_id_different_symbols_do_not_merge():
    legs = [
        _leg(1, "AAA", T0, T0 + timedelta(hours=1), 1, 100, 110),
        _leg(1, "BBB", T0, T0 + timedelta(hours=1), 1, 100, 90),
    ]
    assert {p.symbol for p in positions_from_trades(legs)} == {"AAA", "BBB"}


# --- simulate_portfolio --------------------------------------------------

def test_single_position_realizes_pnl():
    p = _pos("AAA", T0, 100.0, [PositionExit(T0 + timedelta(hours=2), 150.0, 1.0, "historical", True)])
    res = simulate_portfolio([p], _full_size_config(equity=1000.0))
    assert res.positions_taken == 1
    # budget 1000 // 100 = 10 contracts; (150-100)*10 = +500
    assert res.ending_equity == 1500.0
    assert res.return_pct == 0.5
    assert res.priced_historical == 1


def test_second_position_skipped_when_capital_is_committed():
    a = _pos("AAA", T0, 100.0, [PositionExit(T0 + timedelta(hours=5), 100.0, 1.0, "historical", True)])
    b = _pos("BBB", T0 + timedelta(hours=1), 100.0, [PositionExit(T0 + timedelta(hours=6), 200.0, 1.0, "historical", True)])
    res = simulate_portfolio([a, b], _full_size_config(equity=1000.0))
    assert res.positions_taken == 1
    assert res.positions_skipped_capital == 1  # A holds all the cash when B wants in


def test_capital_freed_on_close_is_reusable_same_timestamp():
    ts = T0 + timedelta(hours=3)
    a = _pos("AAA", T0, 100.0, [PositionExit(ts, 120.0, 1.0, "historical", True)])       # closes at ts
    b = _pos("BBB", ts, 100.0, [PositionExit(ts + timedelta(hours=2), 130.0, 1.0, "historical", True)])  # opens at ts
    res = simulate_portfolio([a, b], _full_size_config(equity=1000.0))
    assert res.positions_taken == 2  # close sorts before open at ts


def test_slot_cap_is_enforced():
    # 5% of equity per position => capital alone allows ~20; the slot cap binds first.
    kelly = KellySizer(kelly_fraction=1.0, min_sample_size=1, fallback_fraction=0.05, max_position_fraction=0.05)
    cfg = PortfolioConfig(starting_equity=100_000.0, kelly_sizer=kelly, max_concurrent_positions=3)
    cands = [
        _pos(f"S{i}", T0 + timedelta(minutes=i), 10.0,
             [PositionExit(T0 + timedelta(days=1), 10.0, 1.0, "historical", True)])
        for i in range(5)
    ]
    res = simulate_portfolio(cands, cfg)
    assert res.positions_taken == 3
    assert res.positions_skipped_slots == 2
    assert res.peak_concurrent == 3


def test_untradeable_sub_dollar_contract_is_skipped():
    p = _pos("AAA", T0, 0.5, [PositionExit(T0 + timedelta(hours=1), 5.0, 1.0, "historical", True)])
    res = simulate_portfolio([p], _full_size_config())
    assert res.positions_taken == 0
    assert res.positions_skipped_untradeable == 1


def test_scale_out_partial_then_final_leg():
    exits = [
        PositionExit(T0 + timedelta(hours=1), 200.0, 0.5, "historical", False),
        PositionExit(T0 + timedelta(hours=2), 50.0, 0.5, "ffill", True),
    ]
    p = _pos("AAA", T0, 100.0, exits)
    res = simulate_portfolio([p], _full_size_config(equity=1000.0))
    # 10 contracts; close 5 @200 (+500), then 5 @50 (-250) => +250
    assert res.ending_equity == 1250.0
    assert res.priced_historical == 1 and res.priced_ffill == 1


def test_drawdown_and_compounding_after_a_loss_then_win():
    kelly = KellySizer(kelly_fraction=1.0, min_sample_size=1, fallback_fraction=0.5, max_position_fraction=1.0)
    cfg = PortfolioConfig(starting_equity=1000.0, kelly_sizer=kelly, max_concurrent_positions=5)
    loser = _pos("AAA", T0, 100.0, [PositionExit(T0 + timedelta(hours=1), 0.0, 1.0, "historical", True)])
    winner = _pos("BBB", T0 + timedelta(hours=2), 100.0, [PositionExit(T0 + timedelta(hours=3), 300.0, 1.0, "historical", True)])
    res = simulate_portfolio([loser, winner], cfg)
    # loser: 0.5*1000=500 budget -> 5 contracts -> -500. equity 500, drawdown 50%.
    assert res.realized[0][1] == -500.0
    assert round(res.max_drawdown_pct, 3) == 0.5
    assert res.ending_equity > 500.0  # winner recovered some
