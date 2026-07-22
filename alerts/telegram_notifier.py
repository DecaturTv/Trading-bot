import httpx

from utils.retry import retry

from .base import Notifier
from .models import Alert, Severity

_EMOJI_BY_SEVERITY = {
    Severity.INFO: "ℹ️",
    Severity.WARNING: "⚠️",
    Severity.CRITICAL: "🚨",
}


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, http_client: httpx.AsyncClient | None = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = http_client or httpx.AsyncClient()

    @retry(max_attempts=3, base_delay=0.5, exceptions=(httpx.HTTPError,))
    async def send(self, alert: Alert) -> None:
        text = f"{_EMOJI_BY_SEVERITY[alert.severity]} *{alert.title}*\n{alert.message}"
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        response = await self._client.post(url, json={"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"})
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
