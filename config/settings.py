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

    @model_validator(mode="after")
    def _enforce_live_trading_gate(self) -> "Settings":
        if self.trading_mode == "live" and not self.live_risk_ack:
            raise ValueError(
                "TRADING_MODE=live requires I_UNDERSTAND_LIVE_RISK=true to also be set explicitly."
            )
        return self

    @field_validator("confidence_threshold")
    @classmethod
    def _validate_confidence_threshold(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("confidence_threshold must be between 0 and 100")
        return v

    @field_validator("kelly_fraction")
    @classmethod
    def _validate_kelly_fraction(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("kelly_fraction must be in (0, 1]")
        return v

    @field_validator("daily_loss_limit_pct", "weekly_loss_limit_pct")
    @classmethod
    def _validate_loss_limit_pct(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("loss limit percentages must be in (0, 1]")
        return v
