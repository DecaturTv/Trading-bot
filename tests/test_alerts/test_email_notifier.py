import smtplib
from datetime import datetime, timezone

import pytest

from alerts.email_notifier import EmailNotifier
from alerts.models import Alert, Severity


def make_alert(severity=Severity.WARNING):
    return Alert(title="Halt", message="daily loss limit breached", severity=severity, timestamp=datetime.now(timezone.utc))


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_args = None
        self.sent_message = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.starttls_called = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.sent_message = message


def make_notifier(**overrides):
    defaults = dict(
        smtp_host="smtp.example.com",
        smtp_port=587,
        username="user",
        password="pass",
        from_address="bot@example.com",
        to_address="me@example.com",
        smtp_client_factory=FakeSMTP,
    )
    defaults.update(overrides)
    return EmailNotifier(**defaults)


@pytest.mark.asyncio
async def test_send_calls_starttls_login_and_send_message():
    FakeSMTP.instances.clear()
    notifier = make_notifier()

    await notifier.send(make_alert())

    assert len(FakeSMTP.instances) == 1
    instance = FakeSMTP.instances[0]
    assert instance.starttls_called is True
    assert instance.login_args == ("user", "pass")
    assert instance.sent_message["To"] == "me@example.com"
    assert instance.sent_message["From"] == "bot@example.com"
    assert "Halt" in instance.sent_message["Subject"]
    assert instance.sent_message.get_content().strip() == "daily loss limit breached"


@pytest.mark.asyncio
async def test_send_skips_starttls_when_disabled():
    FakeSMTP.instances.clear()
    notifier = make_notifier(use_tls=False)

    await notifier.send(make_alert())

    assert FakeSMTP.instances[0].starttls_called is False


@pytest.mark.asyncio
async def test_send_retries_on_transient_smtp_failure(monkeypatch):
    async def no_sleep(_):
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    FakeSMTP.instances.clear()

    calls = {"count": 0}

    def flaky_factory(host, port, timeout=None):
        calls["count"] += 1
        if calls["count"] < 2:
            raise smtplib.SMTPConnectError(421, "temporary failure")
        return FakeSMTP(host, port, timeout)

    notifier = make_notifier(smtp_client_factory=flaky_factory)

    await notifier.send(make_alert())

    assert calls["count"] == 2
    assert len(FakeSMTP.instances) == 1
