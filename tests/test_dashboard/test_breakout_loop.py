from datetime import date, datetime, timezone

import pytest
from dash_factories import make_account, make_bars, make_context, make_position_record

from broker.models import OptionContract, OptionGreeks, OptionRight
from decision_engine.models import FactorScore, TradeDirection, TradeSignal
from risk.kelly import KellyResult

from dashboard.breakout_loop import (
    breakout_entry_cycle,
    breakout_loss_limit_check_cycle,
    breakout_position_management_cycle,
)

MARKET_OPEN_TUESDAY = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)
# ~20 calendar days out — inside breakout_loop._TARGET_DTE (20) so the DTE
# deviation guard passes with the default option_max_dte_deviation_days=30.
EXPIRY = date(2026, 8, 10)


def make_chain(strikes_deltas, right=OptionRight.CALL, expiration=EXPIRY):
    return [
        OptionContract(
            symbol=f"AAPL{expiration.strftime('%y%m%d')}{'C' if right is OptionRight.CALL else 'P'}{int(s * 1000):08d}",
            underlying_symbol="AAPL", strike=s, expiration=expiration, right=right,
            bid=5.0, ask=5.2, last_price=5.1, implied_volatility=0.3,
            greeks=OptionGreeks(delta=d, gamma=0.02, theta=-0.05, vega=0.1, rho=0.01),
        )
        for s, d in strikes_deltas
    ]


def breakout_signal(confidence=95.0, direction=TradeDirection.BULLISH):
    return TradeSignal(
        symbol="AAPL", direction=direction, confidence=confidence,
        factors=[FactorScore(name="gap", value=0.9, weight=0.4)], meets_threshold=confidence >= 58,
    )


class _PassingCheck:
    passed = True
    checks: list = []


def _wire_entry(context):
    context.universe_manager.get_universe.return_value = ["AAPL"]
    context.bars_repository.get_bars.return_value = make_bars(n=40)
    context.breakout_decision_model.score.return_value = breakout_signal()
    context.broker.get_option_chain.return_value = make_chain([(95, 0.65), (100, 0.50), (105, 0.35)])
    context.pre_trade_checker.evaluate.return_value = _PassingCheck()
    context.breakout_kelly_sizer.size.return_value = KellyResult(
        full_kelly_fraction=0.1, position_fraction=0.1, used_fallback=True
    )


@pytest.mark.asyncio
async def test_entry_opens_a_breakout_position_not_a_default_one():
    context = make_context()
    _wire_entry(context)
    events = []

    await breakout_entry_cycle(context, MARKET_OPEN_TUESDAY, on_event=events.append)

    context.executor.execute.assert_awaited_once()
    context.breakout_position_repository.upsert.assert_awaited_once()
    context.position_repository.upsert.assert_not_awaited()  # the momentum loop's table is untouched
    assert events[0]["type"] == "breakout_position_opened"
    # signal confirmation is keyed on the breakout vehicle
    vehicle = context.signal_confirmation_repository.upsert.await_args.args[1]
    assert vehicle == "options_breakout"


@pytest.mark.asyncio
async def test_entry_uses_the_breakout_model_and_threshold():
    context = make_context()
    _wire_entry(context)
    # confidence 60 clears breakout's 58 floor but would miss the momentum loop's 92
    context.breakout_decision_model.score.return_value = breakout_signal(confidence=60.0)

    await breakout_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.breakout_decision_model.score.assert_called_once()
    context.decision_model.score.assert_not_called()
    _, _, _, threshold = context.breakout_decision_model.score.call_args.args
    assert threshold == 58.0
    context.executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_entry_skips_symbol_already_held_by_breakout():
    context = make_context()
    _wire_entry(context)
    context.breakout_position_repository.get.return_value = make_position_record(symbol="AAPL")

    await breakout_entry_cycle(context, MARKET_OPEN_TUESDAY)

    context.executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_exit_records_outcome_under_breakout_asset_class():
    context = make_context()
    record = make_position_record(symbol="AAPL", qty=2, entry_cost=500.0, expiration=EXPIRY)
    leg_symbol = record.legs[0].symbol
    context.breakout_position_repository.get_all.return_value = [record]
    context.broker.get_option_chain.return_value = [
        OptionContract(
            symbol=leg_symbol, underlying_symbol="AAPL", strike=150.0, expiration=EXPIRY, right=OptionRight.CALL,
            bid=0.5, ask=0.6, last_price=0.55, implied_volatility=0.3,
            greeks=OptionGreeks(delta=0.1, gamma=0.02, theta=-0.05, vega=0.1, rho=0.01),
        )
    ]

    await breakout_position_management_cycle(context, MARKET_OPEN_TUESDAY)

    context.trade_outcome_repository.record_outcome.assert_awaited_once()
    assert context.trade_outcome_repository.record_outcome.await_args.kwargs["asset_class"] == "breakout"
    context.breakout_position_repository.delete.assert_awaited_once_with("AAPL")


@pytest.mark.asyncio
async def test_paper_loss_limit_notifies_on_breakout_scope_with_dedup_key():
    context = make_context()
    context.settings.trading_mode = "paper"
    context.settings.daily_loss_limit_pct = 0.05
    context.settings.weekly_loss_limit_pct = 0.10
    context.broker.get_account.return_value = make_account(equity=5000.0)
    # -6% day on the $5,000 breakout account
    context.trade_outcome_repository.recent_pnls.return_value = []
    context.trade_outcome_repository.pnls_since.return_value = [-300.0]

    await breakout_loss_limit_check_cycle(context, MARKET_OPEN_TUESDAY)

    context.alert_manager.send.assert_awaited_once()
    alert = context.alert_manager.send.await_args.args[0]
    assert "breakout" in alert.title.lower()
    assert alert.dedup_key == "loss-limit-breach-breakout-paper"
