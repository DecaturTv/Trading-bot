from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    trading_mode: Literal["paper", "live"] = "paper"
    live_risk_ack: bool = Field(default=False, validation_alias="I_UNDERSTAND_LIVE_RISK")

    # Broker credentials
    alpaca_api_key: str | None = None
    alpaca_secret_key: str | None = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    schwab_client_id: str | None = None
    schwab_client_secret: str | None = None

    oanda_api_key: str | None = None
    oanda_account_id: str | None = None

    # Infra
    database_url: str = "postgresql://localhost:5432/trading_bot"
    redis_url: str = "redis://localhost:6379/0"

    # Account
    account_start_balance: float = 500.0

    # Risk defaults
    confidence_threshold: int = 92
    kelly_fraction: float = 0.25
    daily_loss_limit_pct: float = 0.05
    weekly_loss_limit_pct: float = 0.10

    allowed_option_strategies: tuple[str, ...] = (
        "long_call",
        "long_put",
        "debit_spread_vertical",
        "debit_spread_diagonal",
    )

    # Alert channels (all optional — a channel is only wired up if its
    # required fields are present)
    discord_webhook_url: str | None = None

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    twilio_to_number: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    alert_email_from: str | None = None
    alert_email_to: str | None = None

    # ML tracking (local file-based SQLite store — no tracking server needed)
    mlflow_tracking_uri: str = "sqlite:///mlruns/mlflow.db"

    # Trade management — confirmed project rules (see project memory), not
    # engineering defaults: -50% stop, +100% scale-out, 20% trailing
    # pullback thereafter, force-close 2 trading days before expiry.
    stop_loss_pct: float = 0.50
    profit_target_pct: float = 1.00
    scale_out_fraction: float = 0.50
    trailing_stop_pct: float = 0.20
    min_trading_days_before_expiry: int = 2

    # Option selection for the live entry loop
    option_target_delta: float = 0.15
    option_target_dte: int = 25

    # Autonomous trading loop
    autonomous_trading_enabled: bool = True
    scan_interval_seconds: int = 900
    position_check_interval_seconds: int = 120

    # Discord progress report — opt-in, only runs if a Discord webhook is
    # configured; separate from the severity-gated AlertManager channels
    # since it's a status ping, not an event alert. Drives both the stocks
    # and (if OANDA is configured) forex progress reports, sent separately.
    progress_report_interval_seconds: int = 1800

    # Dashboard — required for any request to succeed (fail-closed: no
    # token configured means no access, not open access)
    dashboard_auth_token: str | None = None

    # Forex — opt-in (requires OANDA credentials above); trades the same
    # asset-agnostic scan/decision engine used for equities, just fed FX
    # candles instead of stock bars. Stop-loss/take-profit are attached to
    # the order and managed by OANDA itself, not polled locally. Scans every
    # tradeable pair OANDA offers, fetched fresh each cycle — not a fixed list.
    forex_confidence_threshold: int = 92
    forex_risk_pct_per_trade: float = 0.02
    forex_stop_atr_multiplier: float = 2.5
    forex_take_profit_r_multiple: float = 2.0
    forex_scan_interval_seconds: int = 300
    forex_position_check_interval_seconds: int = 120

    @model_validator(mode="after")
    def _enforce_live_trading_gate(self) -> "Settings":
        if self.trading_mode == "live" and not self.live_risk_ack:
            raise ValueError(
                "TRADING_MODE=live requires I_UNDERSTAND_LIVE_RISK=true to also be set explicitly."
            )
        return self

    @field_validator("confidence_threshold", "forex_confidence_threshold")
    @classmethod
    def _validate_confidence_threshold(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("confidence_threshold must be between 0 and 100")
        return v

    @field_validator("kelly_fraction", "forex_risk_pct_per_trade")
    @classmethod
    def _validate_unit_fraction(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("must be in (0, 1]")
        return v

    @field_validator("daily_loss_limit_pct", "weekly_loss_limit_pct")
    @classmethod
    def _validate_loss_limit_pct(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("loss limit percentages must be in (0, 1]")
        return v

    @field_validator("stop_loss_pct", "profit_target_pct", "trailing_stop_pct")
    @classmethod
    def _validate_positive_pct(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("scale_out_fraction")
    @classmethod
    def _validate_scale_out_fraction(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("scale_out_fraction must be in (0, 1]")
        return v

    @field_validator("min_trading_days_before_expiry")
    @classmethod
    def _validate_min_dte(cls, v: int) -> int:
        if v < 0:
            raise ValueError("min_trading_days_before_expiry must be >= 0")
        return v

    @field_validator("option_target_delta")
    @classmethod
    def _validate_target_delta(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("option_target_delta must be in (0, 1]")
        return v

    @field_validator(
        "option_target_dte",
        "scan_interval_seconds",
        "position_check_interval_seconds",
        "progress_report_interval_seconds",
        "forex_scan_interval_seconds",
        "forex_position_check_interval_seconds",
    )
    @classmethod
    def _validate_positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("forex_stop_atr_multiplier", "forex_take_profit_r_multiple")
    @classmethod
    def _validate_positive_float(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be positive")
        return v
