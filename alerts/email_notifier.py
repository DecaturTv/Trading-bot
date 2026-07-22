import asyncio
import smtplib
from email.message import EmailMessage

from utils.retry import retry

from .base import Notifier
from .models import Alert


class EmailNotifier(Notifier):
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_address: str,
        to_address: str,
        use_tls: bool = True,
        smtp_client_factory=smtplib.SMTP,
    ):
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._username = username
        self._password = password
        self._from_address = from_address
        self._to_address = to_address
        self._use_tls = use_tls
        self._smtp_client_factory = smtp_client_factory

    @retry(max_attempts=3, base_delay=0.5, exceptions=(smtplib.SMTPException, OSError))
    async def send(self, alert: Alert) -> None:
        await asyncio.to_thread(self._send_sync, alert)

    def _send_sync(self, alert: Alert) -> None:
        message = EmailMessage()
        message["Subject"] = f"[{alert.severity.value.upper()}] {alert.title}"
        message["From"] = self._from_address
        message["To"] = self._to_address
        message.set_content(alert.message)

        with self._smtp_client_factory(self._smtp_host, self._smtp_port, timeout=10) as smtp:
            if self._use_tls:
                smtp.starttls()
            smtp.login(self._username, self._password)
            smtp.send_message(message)
