from config.settings import Settings

from .discord_notifier import DiscordNotifier
from .email_notifier import EmailNotifier
from .manager import AlertManager, ChannelRoute
from .models import Severity
from .sms_notifier import SMSNotifier
from .telegram_notifier import TelegramNotifier


def build_alert_manager(
    settings: Settings,
    discord_min_severity: Severity = Severity.INFO,
    telegram_min_severity: Severity = Severity.INFO,
    sms_min_severity: Severity = Severity.CRITICAL,
    email_min_severity: Severity = Severity.WARNING,
) -> AlertManager:
    """Wires up only the channels whose required config is fully present —
    each channel is opt-in, not required. Default min_severity per channel
    reflects channel cost/noise: SMS is reserved for CRITICAL, Discord/
    Telegram (typically the primary monitoring channel) get everything.
    """
    routes: list[ChannelRoute] = []

    if settings.discord_webhook_url:
        routes.append(ChannelRoute(DiscordNotifier(settings.discord_webhook_url), discord_min_severity))

    if settings.telegram_bot_token and settings.telegram_chat_id:
        routes.append(
            ChannelRoute(
                TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id), telegram_min_severity
            )
        )

    if all(
        [settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number, settings.twilio_to_number]
    ):
        routes.append(
            ChannelRoute(
                SMSNotifier(
                    settings.twilio_account_sid,
                    settings.twilio_auth_token,
                    settings.twilio_from_number,
                    settings.twilio_to_number,
                ),
                sms_min_severity,
            )
        )

    if all(
        [settings.smtp_host, settings.smtp_username, settings.smtp_password, settings.alert_email_from, settings.alert_email_to]
    ):
        routes.append(
            ChannelRoute(
                EmailNotifier(
                    settings.smtp_host,
                    settings.smtp_port,
                    settings.smtp_username,
                    settings.smtp_password,
                    settings.alert_email_from,
                    settings.alert_email_to,
                ),
                email_min_severity,
            )
        )

    return AlertManager(routes)
