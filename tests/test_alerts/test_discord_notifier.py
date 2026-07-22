import json
from datetime import datetime, timezone

import httpx
import pytest

from alerts.discord_notifier import DiscordNotifier
from alerts.models import Alert, Severity


def make_alert(severity=Severity.INFO):
    return Alert(title="Test Alert", message="something happened", severity=severity, timestamp=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_send_posts_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = DiscordNotifier("https://discord.example/webhook", http_client=client)

    await notifier.send(make_alert(Severity.WARNING))

    assert captured["url"] == "https://discord.example/webhook"
    embed = captured["json"]["embeds"][0]
    assert embed["title"] == "Test Alert"
    assert embed["description"] == "something happened"
    assert embed["color"] == 0xF1C40F

    await client.aclose()


@pytest.mark.asyncio
async def test_send_retries_on_failure_then_succeeds(monkeypatch):
    async def no_sleep(_):
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 2:
            return httpx.Response(500)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = DiscordNotifier("https://discord.example/webhook", http_client=client)

    await notifier.send(make_alert())

    assert calls["count"] == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_send_raises_after_exhausting_retries(monkeypatch):
    async def no_sleep(_):
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = DiscordNotifier("https://discord.example/webhook", http_client=client)

    with pytest.raises(httpx.HTTPError):
        await notifier.send(make_alert())

    await client.aclose()
