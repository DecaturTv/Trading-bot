import pytest
from pydantic import ValidationError

from config.settings import Settings


def test_defaults_are_paper_mode():
    settings = Settings(_env_file=None)
    assert settings.trading_mode == "paper"
    assert settings.live_risk_ack is False
    assert settings.confidence_threshold == 92
    assert settings.kelly_fraction == 0.25


def test_live_mode_without_ack_raises():
    with pytest.raises(ValidationError, match="I_UNDERSTAND_LIVE_RISK"):
        Settings(_env_file=None, trading_mode="live")


def test_live_mode_with_ack_succeeds():
    settings = Settings(_env_file=None, trading_mode="live", I_UNDERSTAND_LIVE_RISK=True)
    assert settings.trading_mode == "live"
    assert settings.live_risk_ack is True


def test_live_mode_via_env_vars(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("I_UNDERSTAND_LIVE_RISK", "true")
    settings = Settings(_env_file=None)
    assert settings.trading_mode == "live"
    assert settings.live_risk_ack is True


@pytest.mark.parametrize("value", [-1, 101])
def test_confidence_threshold_out_of_range_raises(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, confidence_threshold=value)


@pytest.mark.parametrize("value", [0, -0.1, 1.5])
def test_kelly_fraction_out_of_range_raises(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, kelly_fraction=value)


def test_allowed_option_strategies_excludes_collateral_heavy_strategies():
    settings = Settings(_env_file=None)
    assert "covered_call" not in settings.allowed_option_strategies
    assert "cash_secured_put" not in settings.allowed_option_strategies
    assert "long_call" in settings.allowed_option_strategies


def test_alert_channels_default_to_unconfigured():
    settings = Settings(_env_file=None)
    assert settings.discord_webhook_url is None
    assert settings.telegram_bot_token is None
    assert settings.twilio_account_sid is None
    assert settings.smtp_host is None
    assert settings.smtp_port == 587
