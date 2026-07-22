import asyncio
import logging
from dataclasses import dataclass

from .base import Notifier
from .models import SEVERITY_ORDER, Alert, Severity

logger = logging.getLogger(__name__)


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
    """

    def __init__(self, routes: list[ChannelRoute]):
        self._routes = routes

    async def send(self, alert: Alert) -> None:
        applicable = [r for r in self._routes if SEVERITY_ORDER[alert.severity] >= SEVERITY_ORDER[r.min_severity]]
        if not applicable:
            return
        results = await asyncio.gather(*(r.notifier.send(alert) for r in applicable), return_exceptions=True)
        for route, result in zip(applicable, results):
            if isinstance(result, Exception):
                logger.error("failed to send alert via %s: %s", type(route.notifier).__name__, result)
