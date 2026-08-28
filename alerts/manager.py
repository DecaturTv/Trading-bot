import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .base import Notifier
from .models import SEVERITY_ORDER, Alert, Severity

logger = logging.getLogger(__name__)

# A cycle that keeps re-raising the same alert while a condition stays true
# (paper-mode loss-limit breaches are the motivating case: they run every
# position_check_interval and never halt) would otherwise fire an alert
# every couple of minutes for the rest of the day. Alerts carrying a
# dedup_key are collapsed to at most one per key per this window.
DEFAULT_RESEND_INTERVAL = timedelta(hours=6)


@dataclass(frozen=True)
class ChannelRoute:
    notifier: Notifier
    min_severity: Severity = Severity.INFO


class AlertManager:
    """Fans an alert out to every channel whose min_severity it clears.

    One channel failing to send must never prevent the others from sending,
    and must never propagate back to the caller — a failed Discord webhook
    is not a reason to fail the trading logic that raised the alert in the
    first place. Failures are logged, not raised.

    Alerts with a dedup_key are throttled: the first is sent, repeats sharing
    the key are dropped until resend_interval has elapsed (measured from the
    last alert actually sent, using the alert's own timestamp as the clock).
    The throttle table is in-memory, so a process restart lets one repeat
    through — acceptable, and arguably a useful "still breached" reminder.
    """

    def __init__(self, routes: list[ChannelRoute], resend_interval: timedelta = DEFAULT_RESEND_INTERVAL):
        self._routes = routes
        self._resend_interval = resend_interval
        self._last_sent: dict[str, datetime] = {}

    async def send(self, alert: Alert) -> None:
        if alert.dedup_key is not None:
            last = self._last_sent.get(alert.dedup_key)
            if last is not None and alert.timestamp - last < self._resend_interval:
                logger.debug(
                    "suppressing repeat alert %r (last sent %s, within %s)",
                    alert.dedup_key, last, self._resend_interval,
                )
                return
            self._last_sent[alert.dedup_key] = alert.timestamp

        applicable = [r for r in self._routes if SEVERITY_ORDER[alert.severity] >= SEVERITY_ORDER[r.min_severity]]
        if not applicable:
            return
        results = await asyncio.gather(*(r.notifier.send(alert) for r in applicable), return_exceptions=True)
        for route, result in zip(applicable, results):
            if isinstance(result, Exception):
                logger.error("failed to send alert via %s: %s", type(route.notifier).__name__, result)
