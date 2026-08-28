from datetime import date, datetime, timedelta, timezone

from broker.models import OptionRight, OrderSide

from backtesting.option_quote_source import (
    FFILL,
    HISTORICAL,
    SIMULATED,
    HistoricalOptionQuoteSource,
    SimulatedOptionQuoteSource,
    _ContractSeries,
)
from backtesting.simulated_pricing import SimulatedLeg

EXPIRY = date(2026, 9, 18)
T0 = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)


def _series(occ, strike, closes_at):
    ts = sorted(closes_at)
    return _ContractSeries(
        occ_symbol=occ, strike=strike, expiration=EXPIRY, right=OptionRight.CALL,
        timestamps=ts, closes=[float(i + 1) for i in range(len(ts))],
    )


def _source(*series, max_ffill_bars=3):
    return HistoricalOptionQuoteSource(list(series), timedelta(minutes=15), max_ffill_bars=max_ffill_bars)


def test_mark_exact_bar_is_historical():
    src = _source(_series("O1", 200.0, [T0, T0 + timedelta(minutes=15)]))
    leg = SimulatedLeg(strike=200.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY, occ_symbol="O1")
    res = src.mark([leg], underlying_price=205.0, as_of=T0 + timedelta(minutes=15), volatility=0.3)
    assert res is not None and res.source == HISTORICAL
    assert res.value_per_unit == 2.0 * 100  # 2nd close, x100 contract multiplier


def test_mark_forward_fills_within_window_then_gives_up():
    src = _source(_series("O1", 200.0, [T0]), max_ffill_bars=3)
    leg = SimulatedLeg(strike=200.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY, occ_symbol="O1")

    ffilled = src.mark([leg], 205.0, T0 + timedelta(minutes=45), 0.3)  # 3 bars later — within window
    assert ffilled is not None and ffilled.source == FFILL and ffilled.value_per_unit == 100.0

    gone = src.mark([leg], 205.0, T0 + timedelta(minutes=60), 0.3)  # 4 bars later — past window
    assert gone is None


def test_mark_none_when_contract_unknown_or_leg_unmapped():
    src = _source(_series("O1", 200.0, [T0]))
    unmapped = SimulatedLeg(strike=200.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY)
    assert src.mark([unmapped], 205.0, T0, 0.3) is None
    other = SimulatedLeg(strike=1.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY, occ_symbol="NOPE")
    assert src.mark([other], 205.0, T0, 0.3) is None


def test_select_leg_picks_nearest_delta_strike_that_has_a_quote():
    # 200 is nearest the money (~0.5 delta target) but has no bar at T0;
    # 210 does. select_leg should walk out to 210.
    src = _source(
        _series("O200", 200.0, [T0 - timedelta(days=1)]),
        _series("O210", 210.0, [T0]),
    )
    leg = src.select_leg(
        underlying="AAPL", as_of=T0, target_expiration=EXPIRY, right=OptionRight.CALL,
        target_delta=0.5, underlying_price=200.0, volatility=0.3,
    )
    assert leg is not None and leg.occ_symbol == "O210" and leg.strike == 210.0
    assert leg.expiration == EXPIRY


def test_select_leg_returns_none_when_no_contract_has_a_quote():
    src = _source(_series("O200", 200.0, [T0 - timedelta(days=5)]), max_ffill_bars=1)
    leg = src.select_leg(
        underlying="AAPL", as_of=T0, target_expiration=EXPIRY, right=OptionRight.CALL,
        target_delta=0.5, underlying_price=200.0, volatility=0.3,
    )
    assert leg is None


def test_last_mark_returns_stale_price_for_forced_close():
    src = _source(_series("O1", 200.0, [T0]), max_ffill_bars=1)
    leg = SimulatedLeg(strike=200.0, expiration=EXPIRY, right=OptionRight.CALL, side=OrderSide.BUY, occ_symbol="O1")
    # Way past the ffill window: mark() gives up, last_mark() still returns the last close.
    assert src.mark([leg], 205.0, T0 + timedelta(days=2), 0.3) is None
    stale = src.last_mark([leg], 205.0, T0 + timedelta(days=2), 0.3)
    assert stale is not None and stale.value_per_unit == 100.0


def test_simulated_source_always_prices_and_selects():
    src = SimulatedOptionQuoteSource()
    leg = src.select_leg(
        underlying="AAPL", as_of=T0, target_expiration=EXPIRY, right=OptionRight.CALL,
        target_delta=0.3, underlying_price=200.0, volatility=0.3,
    )
    assert leg is not None and leg.occ_symbol is None
    res = src.mark([leg], 200.0, T0, 0.3)
    assert res is not None and res.source == SIMULATED and res.value_per_unit > 0
