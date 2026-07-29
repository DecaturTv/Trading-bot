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
    confidence_threshold: int = 90
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
    # Require a stop-loss breach to hold for this many consecutive
    # position-check cycles (position_check_interval_seconds apart) before
    # closing. Added 2026-07-27 after the ACI trade closed on a single noisy
    # quote — the underlying hadn't actually confirmed a move against the
    # position. See project memory.
    stop_loss_confirmation_count: int = 2
    # Require a trade signal (entry, or a reversal against an open position)
    # to hold in the same direction for this many consecutive scan/check
    # cycles before acting on it — a single-tick whipsaw shouldn't be enough
    # to open or reverse a position. Added 2026-07-28 after two consecutive
    # days of severe daily-loss-limit breaches traced to noisy single-scan
    # signal flips. Applies to both new entries and the reversal-exit check
    # on open positions (see TradeManagementConfig.reversal_confirmation_count).
    signal_confirmation_count: int = 3

    # Option selection for the live entry loop
    option_target_delta: float = 0.15
    option_target_dte: int = 25
    # Skip an entry if the closest available expiration is more than this
    # many calendar days from option_target_dte — a symbol's chain may have
    # nothing near the target, and select_expiration() always returns its
    # closest match regardless of how far off that is. Without this, a
    # ~25 DTE target can silently fall back to a several-days-out contract
    # with far more theta/gamma risk than intended. Added 2026-07-27, same
    # incident as stop_loss_confirmation_count above.
    option_max_dte_deviation_days: int = 10

    # Autonomous trading loop
    autonomous_trading_enabled: bool = True
    scan_interval_seconds: int = 900
    position_check_interval_seconds: int = 120

    # Intraday entry cycles — run alongside the daily one (see build_scheduler),
    # each on its own timeframe/interval, so a symbol can signal off a faster
    # setup instead of waiting for a daily close. Interval matches each
    # timeframe's own bar duration: no point rescanning 1h bars every 5min.
    intraday_5m_scan_interval_seconds: int = 300
    intraday_15m_scan_interval_seconds: int = 900
    intraday_1h_scan_interval_seconds: int = 3600

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
    # Lowered from 2.0 on 2026-07-25: replaying real closed trades against
    # OANDA bid/ask history showed most of them make a real favorable move
    # early, then reverse before reaching 2R -- 0.5R was near breakeven
    # (-0.5R net vs -5R net at 2.0R) but on a small, correlated sample, so
    # 1.0 is a conservative step in that direction rather than the full move.
    # See project memory.
    forex_take_profit_r_multiple: float = 1.0
    forex_scan_interval_seconds: int = 300
    forex_position_check_interval_seconds: int = 120
    # See forex/exposure.py -- caps how many open positions can share a
    # currency, so correlated pairs (EUR_ZAR + CHF_ZAR + GBP_ZAR, all really
    # one bet on ZAR) can't all stack on the same underlying move.
    forex_max_positions_per_currency: int = 2
    # Kill switch for new forex entries only -- position management,
    # reconciliation, loss-limit checks, and progress reports keep running
    # against whatever's already open. Flip off to pause entries (e.g. while
    # investigating a losing strategy) without abandoning open positions.
    forex_entries_enabled: bool = True

    # Congressional trade disclosures — a new decision_engine factor (see
    # DEFAULT_WEIGHTS in decision_engine/scoring.py) fed by free STOCK Act
    # PTR data (House only for now; see congress/source.py). Members named
    # here count 2x as heavily in the factor; empty means every disclosure
    # counts equally regardless of filer. Only explicitly-requested names are
    # defaulted in here rather than a broader hand-picked list.
    congress_tracked_members: tuple[str, ...] = ("Nancy Pelosi",)
    congress_lookback_days: int = 30
    congress_sync_interval_hours: int = 6

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

    @field_validator("stop_loss_confirmation_count", "signal_confirmation_count")
    @classmethod
    def _validate_stop_loss_confirmation_count(cls, v: int) -> int:
        if v < 1:
            raise ValueError("confirmation counts must be >= 1")
        return v

    @field_validator("option_max_dte_deviation_days")
    @classmethod
    def _validate_option_max_dte_deviation_days(cls, v: int) -> int:
        if v < 0:
            raise ValueError("option_max_dte_deviation_days must be >= 0")
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
        "intraday_5m_scan_interval_seconds",
        "intraday_15m_scan_interval_seconds",
        "intraday_1h_scan_interval_seconds",
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
