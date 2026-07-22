import json
from datetime import datetime, timezone

import httpx
import pytest

from alerts.models import Alert, Severity
from alerts.telegram_notifier import TelegramNotifier


def make_alert(severity=Severity.CRITICAL):
    return Alert(title="Halt", message="daily loss limit breached", severity=severity, timestamp=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_send_posts_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = TelegramNotifier("bot-token", "chat-123", http_client=client)

    await notifier.send(make_alert())

    assert captured["url"] == "https://api.telegram.org/botbot-token/sendMessage"
    assert captured["json"]["chat_id"] == "chat-123"
    assert "Halt" in captured["json"]["text"]
    assert "daily loss limit breached" in captured["json"]["text"]

    await client.aclose()


@pytest.mark.asyncio
async def test_send_raises_on_non_2xx(monkeypatch):
    async def no_sleep(_):
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = TelegramNotifier("bot-token", "chat-123", http_client=client)

    with pytest.raises(httpx.HTTPError):
        await notifier.send(make_alert())

    await client.aclose()
