from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from alerts.models import Alert, Severity
from alerts.sms_notifier import SMSNotifier


def make_alert(message="short message", severity=Severity.CRITICAL):
    return Alert(title="Halt", message=message, severity=severity, timestamp=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_send_posts_expected_form_body_with_basic_auth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = parse_qs(request.content.decode())
        captured["auth_header"] = request.headers.get("authorization")
        return httpx.Response(201)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = SMSNotifier("AC123", "authtoken", "+15551234567", "+15557654321", http_client=client)

    await notifier.send(make_alert())

    assert captured["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert captured["body"]["From"] == ["+15551234567"]
    assert captured["body"]["To"] == ["+15557654321"]
    assert "short message" in captured["body"]["Body"][0]
    assert captured["auth_header"] is not None and captured["auth_header"].startswith("Basic ")

    await client.aclose()


@pytest.mark.asyncio
async def test_send_truncates_long_messages():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = parse_qs(request.content.decode())
        return httpx.Response(201)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = SMSNotifier("AC123", "authtoken", "+15551234567", "+15557654321", http_client=client)

    await notifier.send(make_alert(message="x" * 1000))

    body = captured["body"]["Body"][0]
    assert len(body) <= 480
    assert body.endswith("…")

    await client.aclose()


@pytest.mark.asyncio
async def test_send_raises_on_failure(monkeypatch):
    async def no_sleep(_):
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = SMSNotifier("AC123", "authtoken", "+15551234567", "+15557654321", http_client=client)

    with pytest.raises(httpx.HTTPError):
        await notifier.send(make_alert())

    await client.aclose()
