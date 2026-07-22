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


def test_mlflow_tracking_uri_defaults_to_local_sqlite():
    settings = Settings(_env_file=None)
    assert settings.mlflow_tracking_uri == "sqlite:///mlruns/mlflow.db"


def test_trade_management_defaults_match_confirmed_project_rules():
    settings = Settings(_env_file=None)
    assert settings.stop_loss_pct == pytest.approx(0.50)
    assert settings.profit_target_pct == pytest.approx(1.00)
    assert settings.scale_out_fraction == pytest.approx(0.50)
    assert settings.trailing_stop_pct == pytest.approx(0.20)
    assert settings.min_trading_days_before_expiry == 2


def test_autonomous_trading_enabled_by_default():
    settings = Settings(_env_file=None)
    assert settings.autonomous_trading_enabled is True


def test_dashboard_auth_token_unset_by_default():
    settings = Settings(_env_file=None)
    assert settings.dashboard_auth_token is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("stop_loss_pct", 0.0),
        ("profit_target_pct", -0.1),
        ("trailing_stop_pct", 0.0),
        ("scale_out_fraction", 0.0),
        ("scale_out_fraction", 1.5),
        ("min_trading_days_before_expiry", -1),
        ("option_target_delta", 0.0),
        ("option_target_delta", 1.5),
        ("option_target_dte", 0),
        ("scan_interval_seconds", 0),
        ("position_check_interval_seconds", -1),
    ],
)
def test_rejects_invalid_trade_loop_settings(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_alert_channels_default_to_unconfigured():
    settings = Settings(_env_file=None)
    assert settings.discord_webhook_url is None
    assert settings.telegram_bot_token is None
    assert settings.twilio_account_sid is None
    assert settings.smtp_host is None
    assert settings.smtp_port == 587


def test_oanda_credentials_unset_by_default():
    settings = Settings(_env_file=None)
    assert settings.oanda_api_key is None
    assert settings.oanda_account_id is None


def test_forex_defaults():
    settings = Settings(_env_file=None)
    assert settings.forex_confidence_threshold == 92
    assert settings.forex_risk_pct_per_trade == pytest.approx(0.02)
    assert settings.forex_stop_atr_multiplier == pytest.approx(1.5)
    assert settings.forex_take_profit_r_multiple == pytest.approx(2.0)
    assert settings.forex_scan_interval_seconds == 300
    assert settings.forex_position_check_interval_seconds == 120


@pytest.mark.parametrize(
    "field,value",
    [
        ("forex_confidence_threshold", -1),
        ("forex_confidence_threshold", 101),
        ("forex_risk_pct_per_trade", 0.0),
        ("forex_risk_pct_per_trade", 1.5),
        ("forex_stop_atr_multiplier", 0.0),
        ("forex_take_profit_r_multiple", -1.0),
        ("forex_scan_interval_seconds", 0),
        ("forex_position_check_interval_seconds", -1),
    ],
)
def test_rejects_invalid_forex_settings(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
