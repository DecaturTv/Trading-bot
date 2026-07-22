from abc import ABC, abstractmethod

from .models import Alert


class Notifier(ABC):
    """Interface every alert channel implements — the rest of the system
    depends only on this, never on a channel's SDK/API shape directly.
    """

    @abstractmethod
    async def send(self, alert: Alert) -> None: ...
