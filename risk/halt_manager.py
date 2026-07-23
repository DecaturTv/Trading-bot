from datetime import datetime

from .halt_repository import HaltRepository


class HaltManager:
    """Persistent halt state: daily/weekly loss limits act as a circuit
    breaker independent of per-trade sizing, and the halt must survive a
    process restart — losing money isn't a reason to forget you're halted.

    scope separates the two independent accounts this bot trades through
    ("equities" covers both options and direct stock positions, since they
    share the same Alpaca account; "forex" is OANDA) so a loss-limit breach
    on one side halts only that side, not both.
    """

    def __init__(self, repository: HaltRepository):
        self._repository = repository

    async def is_halted(self, scope: str = "equities") -> bool:
        latest = await self._repository.latest_event(scope)
        return latest is not None and latest["action"] == "halt"

    async def halt(self, reason: str, now: datetime, scope: str = "equities") -> None:
        await self._repository.record_event(now, "halt", reason, scope)

    async def resume(self, reason: str, now: datetime, scope: str = "equities") -> None:
        await self._repository.record_event(now, "resume", reason, scope)

    async def check_and_halt_on_loss_limits(
        self,
        daily_pnl_pct: float,
        weekly_pnl_pct: float,
        daily_limit_pct: float,
        weekly_limit_pct: float,
        now: datetime,
        scope: str = "equities",
    ) -> bool:
        if daily_pnl_pct <= -daily_limit_pct:
            await self.halt(f"daily loss limit breached: {daily_pnl_pct:.2%} <= -{daily_limit_pct:.2%}", now, scope)
            return True
        if weekly_pnl_pct <= -weekly_limit_pct:
            await self.halt(f"weekly loss limit breached: {weekly_pnl_pct:.2%} <= -{weekly_limit_pct:.2%}", now, scope)
            return True
        return False
