import httpx

from utils.retry import retry

from .base import Notifier
from .models import Alert

_MAX_SMS_LENGTH = 480  # ~3 SMS segments; avoids runaway multi-segment costs on a long message


class SMSNotifier(Notifier):
    """Uses Twilio's REST API directly rather than their SDK — it's a single
    HTTP POST, not worth an extra dependency for."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._to_number = to_number
        self._client = http_client or httpx.AsyncClient()

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def send(self, alert: Alert) -> None:
        body = f"[{alert.severity.value.upper()}] {alert.title}: {alert.message}"
        if len(body) > _MAX_SMS_LENGTH:
            body = body[: _MAX_SMS_LENGTH - 1] + "…"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Messages.json"
        response = await self._client.post(
            url,
            data={"From": self._from_number, "To": self._to_number, "Body": body},
            auth=(self._account_sid, self._auth_token),
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
