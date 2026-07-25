from datetime import datetime, timezone

from broker.models import OrderSide
from forex.exposure import check_currency_concentration
from forex.models import OpenForexPosition


def make_position(pair):
    return OpenForexPosition(
        pair=pair, side=OrderSide.BUY, units=1000, entry_price=1.0, stop_loss_price=0.99,
        take_profit_price=1.02, oanda_trade_id="1", opened_at=datetime.now(timezone.utc),
    )


def test_passes_when_no_open_positions():
    result = check_currency_concentration("EUR_ZAR", [], max_positions_per_currency=2)
    assert result.passed is True


def test_passes_when_shared_currency_below_cap():
    open_positions = [make_position("CHF_ZAR")]
    result = check_currency_concentration("EUR_ZAR", open_positions, max_positions_per_currency=2)
    assert result.passed is True


def test_rejects_when_quote_currency_at_cap():
    open_positions = [make_position("CHF_ZAR"), make_position("GBP_ZAR")]
    result = check_currency_concentration("EUR_ZAR", open_positions, max_positions_per_currency=2)
    assert result.passed is False
    assert "ZAR" in result.reason


def test_rejects_when_base_currency_at_cap():
    open_positions = [make_position("NZD_CAD"), make_position("NZD_SGD")]
    result = check_currency_concentration("NZD_USD", open_positions, max_positions_per_currency=2)
    assert result.passed is False
    assert "NZD" in result.reason


def test_passes_when_open_positions_share_no_currency():
    open_positions = [make_position("EUR_USD"), make_position("GBP_JPY")]
    result = check_currency_concentration("AUD_CAD", open_positions, max_positions_per_currency=2)
    assert result.passed is True


def test_counts_both_legs_of_an_open_position_independently():
    # ZAR_JPY contributes one count to ZAR and one to JPY -- a candidate on
    # either currency should see it.
    open_positions = [make_position("ZAR_JPY"), make_position("EUR_ZAR")]
    result = check_currency_concentration("GBP_ZAR", open_positions, max_positions_per_currency=2)
    assert result.passed is False
    assert "ZAR" in result.reason


def test_cap_of_one_rejects_any_shared_currency():
    open_positions = [make_position("EUR_USD")]
    result = check_currency_concentration("GBP_USD", open_positions, max_positions_per_currency=1)
    assert result.passed is False
    assert "USD" in result.reason
