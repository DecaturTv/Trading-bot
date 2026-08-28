from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


SEVERITY_ORDER = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


@dataclass(frozen=True)
class Alert:
    title: str
    message: str
    severity: Severity
    timestamp: datetime
    context: dict = field(default_factory=dict)
    # When set, AlertManager collapses repeats sharing this key: the first
    # send goes out, later ones within the resend window are dropped. Use it
    # for alerts raised every cycle by a condition that stays true (e.g. a
    # paper-mode loss-limit breach that never halts), where the message text
    # changes each cycle so title/message can't be the dedup signal.
    dedup_key: str | None = None
