from datetime import datetime

from alerts.models import Alert, Severity

from .context import AppContext

_SCOPES = ("equities", "forex")


async def paper_trading_daily_reset_cycle(context: AppContext, now: datetime) -> None:
    """Paper-trading only: auto-resumes any halt once a day so a bad day's
    damage doesn't compound into an extended lockout while learning and
    iterating -- pairs with loss_limit_check_cycle skipping the weekly check
    in paper mode. Idempotent (resuming an already-resumed scope is a
    no-op), so firing more than once a day is harmless.

    Never runs in live mode: once real capital is on the line, a halt stays
    in effect until a human clears it -- this is intentionally the one
    piece of paper-mode behavior that must NOT carry over when trading_mode
    flips to "live".
    """
    if context.settings.trading_mode != "paper":
        return

    for scope in _SCOPES:
        await context.halt_manager.resume("paper trading daily reset -- new day, fresh start", now, scope)

    await context.alert_manager.send(
        Alert(
            title="Paper trading reset",
            message="New day, fresh $500 baseline -- any halts from yesterday are cleared.",
            severity=Severity.INFO,
            timestamp=now,
        )
    )
