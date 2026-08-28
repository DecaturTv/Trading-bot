from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from alerts.base import Notifier
from alerts.manager import AlertManager, ChannelRoute
from alerts.models import Alert, Severity


def make_alert(severity=Severity.WARNING, *, timestamp=None, dedup_key=None):
    return Alert(
        title="t",
        message="m",
        severity=severity,
        timestamp=timestamp or datetime.now(timezone.utc),
        dedup_key=dedup_key,
    )


def make_notifier():
    return AsyncMock(spec=Notifier)


@pytest.mark.asyncio
async def test_sends_only_to_channels_clearing_min_severity():
    info_channel = make_notifier()
    critical_channel = make_notifier()
    manager = AlertManager(
        [ChannelRoute(info_channel, Severity.INFO), ChannelRoute(critical_channel, Severity.CRITICAL)]
    )

    await manager.send(make_alert(Severity.WARNING))

    info_channel.send.assert_awaited_once()
    critical_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_critical_alert_reaches_all_channels():
    channel_a = make_notifier()
    channel_b = make_notifier()
    manager = AlertManager([ChannelRoute(channel_a, Severity.INFO), ChannelRoute(channel_b, Severity.CRITICAL)])

    await manager.send(make_alert(Severity.CRITICAL))

    channel_a.send.assert_awaited_once()
    channel_b.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_channel_failure_does_not_block_others():
    failing_channel = make_notifier()
    failing_channel.send.side_effect = RuntimeError("webhook down")
    healthy_channel = make_notifier()
    manager = AlertManager([ChannelRoute(failing_channel, Severity.INFO), ChannelRoute(healthy_channel, Severity.INFO)])

    await manager.send(make_alert(Severity.INFO))  # must not raise

    healthy_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_channels_configured_is_a_noop():
    manager = AlertManager([])
    await manager.send(make_alert())  # must not raise


@pytest.mark.asyncio
async def test_keyed_repeats_are_throttled_within_the_resend_window():
    channel = make_notifier()
    manager = AlertManager([ChannelRoute(channel, Severity.INFO)], resend_interval=timedelta(hours=6))
    t0 = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)

    await manager.send(make_alert(timestamp=t0, dedup_key="loss-limit"))
    await manager.send(make_alert(timestamp=t0 + timedelta(minutes=2), dedup_key="loss-limit"))
    await manager.send(make_alert(timestamp=t0 + timedelta(hours=5, minutes=59), dedup_key="loss-limit"))

    channel.send.assert_awaited_once()

    # Past the window, and again for a different key, both go through.
    await manager.send(make_alert(timestamp=t0 + timedelta(hours=7), dedup_key="loss-limit"))
    await manager.send(make_alert(timestamp=t0 + timedelta(minutes=1), dedup_key="other"))
    assert channel.send.await_count == 3


@pytest.mark.asyncio
async def test_alerts_without_a_dedup_key_are_never_throttled():
    channel = make_notifier()
    manager = AlertManager([ChannelRoute(channel, Severity.INFO)])
    t0 = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)

    await manager.send(make_alert(timestamp=t0))
    await manager.send(make_alert(timestamp=t0))

    assert channel.send.await_count == 2
