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
