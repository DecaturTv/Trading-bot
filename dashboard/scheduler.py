from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .context import AppContext
from .forex_loop import forex_entry_cycle, forex_position_management_cycle, forex_progress_report_cycle
from .trading_loop import (
    EventCallback,
    entry_cycle,
    loss_limit_check_cycle,
    position_management_cycle,
    progress_report_cycle,
)


def build_scheduler(context: AppContext, on_event: EventCallback = None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    async def _entry_job():
        await entry_cycle(context, datetime.now(timezone.utc), on_event)

    async def _position_job():
        await position_management_cycle(context, datetime.now(timezone.utc), on_event)

    async def _loss_limit_job():
        await loss_limit_check_cycle(context, datetime.now(timezone.utc))

    async def _progress_report_job():
        await progress_report_cycle(context, datetime.now(timezone.utc))

    async def _forex_entry_job():
        await forex_entry_cycle(context, datetime.now(timezone.utc), on_event)

    async def _forex_position_job():
        await forex_position_management_cycle(context, datetime.now(timezone.utc), on_event)

    async def _forex_progress_report_job():
        await forex_progress_report_cycle(context, datetime.now(timezone.utc))

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
    if context.progress_notifier is not None:
        scheduler.add_job(
            _progress_report_job, IntervalTrigger(seconds=context.settings.progress_report_interval_seconds),
            id="progress_report", max_instances=1, coalesce=True,
        )
    if context.forex_broker is not None:
        scheduler.add_job(
            _forex_entry_job, IntervalTrigger(seconds=context.settings.forex_scan_interval_seconds),
            id="forex_entry_cycle", max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            _forex_position_job, IntervalTrigger(seconds=context.settings.forex_position_check_interval_seconds),
            id="forex_position_management_cycle", max_instances=1, coalesce=True,
        )
        if context.progress_notifier is not None:
            scheduler.add_job(
                _forex_progress_report_job, IntervalTrigger(seconds=context.settings.progress_report_interval_seconds),
                id="forex_progress_report", max_instances=1, coalesce=True,
            )

    return scheduler
