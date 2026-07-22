from alerts.discord_notifier import DiscordNotifier
from alerts.email_notifier import EmailNotifier
from alerts.factory import build_alert_manager
from alerts.sms_notifier import SMSNotifier
from alerts.telegram_notifier import TelegramNotifier
from config.settings import Settings


def test_no_channels_wired_up_when_nothing_configured():
    settings = Settings(_env_file=None)
    manager = build_alert_manager(settings)
    assert manager._routes == []


def test_discord_wired_up_when_webhook_url_present():
    settings = Settings(_env_file=None, discord_webhook_url="https://discord.example/webhook")
    manager = build_alert_manager(settings)
    assert len(manager._routes) == 1
    assert isinstance(manager._routes[0].notifier, DiscordNotifier)


def test_telegram_requires_both_token_and_chat_id():
    settings = Settings(_env_file=None, telegram_bot_token="token")
    manager = build_alert_manager(settings)
    assert manager._routes == []

    settings = Settings(_env_file=None, telegram_bot_token="token", telegram_chat_id="chat")
    manager = build_alert_manager(settings)
    assert len(manager._routes) == 1
    assert isinstance(manager._routes[0].notifier, TelegramNotifier)


def test_sms_requires_all_twilio_fields():
    settings = Settings(
        _env_file=None,
        twilio_account_sid="sid",
        twilio_auth_token="token",
        twilio_from_number="+1",
        # missing twilio_to_number
    )
    manager = build_alert_manager(settings)
    assert manager._routes == []

    settings = Settings(
        _env_file=None,
        twilio_account_sid="sid",
        twilio_auth_token="token",
        twilio_from_number="+1",
        twilio_to_number="+2",
    )
    manager = build_alert_manager(settings)
    assert len(manager._routes) == 1
    assert isinstance(manager._routes[0].notifier, SMSNotifier)


def test_email_requires_all_smtp_fields():
    settings = Settings(
        _env_file=None,
        smtp_host="smtp.example.com",
        smtp_username="user",
        smtp_password="pass",
        alert_email_from="bot@example.com",
        alert_email_to="me@example.com",
    )
    manager = build_alert_manager(settings)
    assert len(manager._routes) == 1
    assert isinstance(manager._routes[0].notifier, EmailNotifier)


def test_multiple_channels_can_be_wired_up_together():
    settings = Settings(
        _env_file=None,
        discord_webhook_url="https://discord.example/webhook",
        telegram_bot_token="token",
        telegram_chat_id="chat",
    )
    manager = build_alert_manager(settings)
    assert len(manager._routes) == 2


def test_default_min_severities():
    settings = Settings(
        _env_file=None,
        discord_webhook_url="https://discord.example/webhook",
        twilio_account_sid="sid",
        twilio_auth_token="token",
        twilio_from_number="+1",
        twilio_to_number="+2",
    )
    manager = build_alert_manager(settings)
    from alerts.models import Severity

    routes_by_type = {type(r.notifier): r.min_severity for r in manager._routes}
    assert routes_by_type[DiscordNotifier] is Severity.INFO
    assert routes_by_type[SMSNotifier] is Severity.CRITICAL
