from datetime import datetime

from .halt_repository import HaltRepository


class HaltManager:
    """Persistent halt state: daily/weekly loss limits act as a circuit
    breaker independent of per-trade sizing, and the halt must survive a
    process restart — losing money isn't a reason to forget you're halted.
    """

    def __init__(self, repository: HaltRepository):
        self._repository = repository

    async def is_halted(self) -> bool:
        latest = await self._repository.latest_event()
        return latest is not None and latest["action"] == "halt"

    async def halt(self, reason: str, now: datetime) -> None:
        await self._repository.record_event(now, "halt", reason)

    async def resume(self, reason: str, now: datetime) -> None:
        await self._repository.record_event(now, "resume", reason)

    async def check_and_halt_on_loss_limits(
        self,
        daily_pnl_pct: float,
        weekly_pnl_pct: float,
        daily_limit_pct: float,
        weekly_limit_pct: float,
        now: datetime,
    ) -> bool:
        if daily_pnl_pct <= -daily_limit_pct:
            await self.halt(f"daily loss limit breached: {daily_pnl_pct:.2%} <= -{daily_limit_pct:.2%}", now)
            return True
        if weekly_pnl_pct <= -weekly_limit_pct:
            await self.halt(f"weekly loss limit breached: {weekly_pnl_pct:.2%} <= -{weekly_limit_pct:.2%}", now)
            return True
        return False
