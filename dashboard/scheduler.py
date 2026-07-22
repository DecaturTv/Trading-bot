from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .context import AppContext
from .trading_loop import EventCallback, entry_cycle, loss_limit_check_cycle, position_management_cycle


def build_scheduler(context: AppContext, on_event: EventCallback = None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    async def _entry_job():
        await entry_cycle(context, datetime.now(timezone.utc), on_event)

    async def _position_job():
        await position_management_cycle(context, datetime.now(timezone.utc), on_event)

    async def _loss_limit_job():
        await loss_limit_check_cycle(context, datetime.now(timezone.utc))

    scheduler.add_job(
        _entry_job, IntervalTrigger(seconds=context.settings.scan_interval_seconds), id="entry_cycle",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _position_job, IntervalTrigger(seconds=context.settings.position_check_interval_seconds),
        id="position_management_cycle", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _loss_limit_job, IntervalTrigger(seconds=context.settings.position_check_interval_seconds),
        id="loss_limit_check", max_instances=1, coalesce=True,
    )

    return scheduler
