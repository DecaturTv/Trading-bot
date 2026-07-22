import httpx

from utils.retry import retry

from .base import Notifier
from .models import Alert, Severity

_COLOR_BY_SEVERITY = {
    Severity.INFO: 0x2ECC71,  # green
    Severity.WARNING: 0xF1C40F,  # yellow
    Severity.CRITICAL: 0xE74C3C,  # red
}


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str, http_client: httpx.AsyncClient | None = None):
        self._webhook_url = webhook_url
        self._client = http_client or httpx.AsyncClient()

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def send(self, alert: Alert) -> None:
        payload = {
            "embeds": [
                {
                    "title": alert.title,
                    "description": alert.message,
                    "color": _COLOR_BY_SEVERITY[alert.severity],
                    "timestamp": alert.timestamp.isoformat(),
                }
            ]
        }
        response = await self._client.post(self._webhook_url, json=payload)
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
