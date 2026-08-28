from datetime import datetime

from .halt_repository import HaltRepository


def evaluate_loss_limits(
    daily_pnl_pct: float, weekly_pnl_pct: float, daily_limit_pct: float, weekly_limit_pct: float
) -> str | None:
    """Pure breach check, no side effects: returns the breach reason string
    if either limit is breached (daily checked first), else None. Shared by
    HaltManager.check_and_halt_on_loss_limits (live trading, actually halts)
    and the paper-trading notify-only path in trading_loop/forex_loop, which
    wants the same threshold logic without acting on it."""
    if daily_pnl_pct <= -daily_limit_pct:
        return f"daily loss limit breached: {daily_pnl_pct:.2%} <= -{daily_limit_pct:.2%}"
    if weekly_pnl_pct <= -weekly_limit_pct:
        return f"weekly loss limit breached: {weekly_pnl_pct:.2%} <= -{weekly_limit_pct:.2%}"
    return None


class HaltManager:
    """Persistent halt state: daily/weekly loss limits act as a circuit
    breaker independent of per-trade sizing, and the halt must survive a
    process restart — losing money isn't a reason to forget you're halted.

    scope separates the two independent accounts this bot trades through
    ("equities" covers both options and direct stock positions, since they
    share the same Alpaca account; "forex" is OANDA) so a loss-limit breach
    on one side halts only that side, not both.
    """

    def __init__(self, repository: HaltRepository, paper_mode: bool = False):
        self._repository = repository
        # Paper trading never actually halts: there's no capital to protect,
        # and stopping entries mid-experiment just starves the very data
        # we're iterating on. Loss limits are still evaluated and alerted on
        # by the loss_limit_check cycles (notify-only) — see project memory
        # and dashboard/trading_loop.loss_limit_check_cycle. Manual /halt
        # calls still record an event (audit trail) but don't gate entries.
        self._paper_mode = paper_mode

    async def is_halted(self, scope: str = "equities") -> bool:
        if self._paper_mode:
            return False
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
        reason = evaluate_loss_limits(daily_pnl_pct, weekly_pnl_pct, daily_limit_pct, weekly_limit_pct)
        if reason is None:
            return False
        await self.halt(reason, now, scope)
        return True
