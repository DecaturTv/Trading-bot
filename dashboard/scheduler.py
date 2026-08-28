from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .context import AppContext
from .forex_loop import (
    forex_entry_cycle,
    forex_loss_limit_check_cycle,
    forex_position_management_cycle,
    forex_progress_report_cycle,
)
from .stock_loop import stock_entry_cycle, stock_position_management_cycle
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

    async def _entry_job_5m():
        await entry_cycle(context, datetime.now(timezone.utc), on_event, timeframe="5Min")

    async def _entry_job_15m():
        await entry_cycle(context, datetime.now(timezone.utc), on_event, timeframe="15Min")

    async def _entry_job_1h():
        await entry_cycle(context, datetime.now(timezone.utc), on_event, timeframe="1Hour")

    async def _position_job():
        await position_management_cycle(context, datetime.now(timezone.utc), on_event)

    async def _stock_entry_job():
        await stock_entry_cycle(context, datetime.now(timezone.utc), on_event)

    async def _stock_position_job():
        await stock_position_management_cycle(context, datetime.now(timezone.utc), on_event)

    async def _loss_limit_job():
        await loss_limit_check_cycle(context, datetime.now(timezone.utc))

    async def _progress_report_job():
        await progress_report_cycle(context, datetime.now(timezone.utc))

    async def _forex_entry_job():
        await forex_entry_cycle(context, datetime.now(timezone.utc), on_event)

    async def _forex_position_job():
        await forex_position_management_cycle(context, datetime.now(timezone.utc), on_event)

    async def _forex_loss_limit_job():
        await forex_loss_limit_check_cycle(context, datetime.now(timezone.utc))

    async def _forex_progress_report_job():
        await forex_progress_report_cycle(context, datetime.now(timezone.utc))

    scheduler.add_job(
        _entry_job, IntervalTrigger(seconds=context.settings.scan_interval_seconds), id="entry_cycle",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _entry_job_5m, IntervalTrigger(seconds=context.settings.intraday_5m_scan_interval_seconds), id="entry_cycle_5m",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _entry_job_15m, IntervalTrigger(seconds=context.settings.intraday_15m_scan_interval_seconds), id="entry_cycle_15m",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _entry_job_1h, IntervalTrigger(seconds=context.settings.intraday_1h_scan_interval_seconds), id="entry_cycle_1h",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _position_job, IntervalTrigger(seconds=context.settings.position_check_interval_seconds),
        id="position_management_cycle", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _stock_entry_job, IntervalTrigger(seconds=context.settings.scan_interval_seconds), id="stock_entry_cycle",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _stock_position_job, IntervalTrigger(seconds=context.settings.position_check_interval_seconds),
        id="stock_position_management_cycle", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _loss_limit_job, IntervalTrigger(seconds=context.settings.position_check_interval_seconds),
        id="loss_limit_check", max_instances=1, coalesce=True,
    )
    if context.progress_notifier is not None:
        # Twice per trading day: a midday check-in and one ~10 min after the
        # close with the day's final numbers. Both cycles gate on the weekday
        # themselves (the close report intentionally fires after 16:00 ET).
        for job_id, hour, minute in (("progress_report_midday", 12, 0), ("progress_report_close", 16, 10)):
            scheduler.add_job(
                _progress_report_job,
                CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="America/New_York"),
                id=job_id, max_instances=1, coalesce=True,
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
        scheduler.add_job(
            _forex_loss_limit_job, IntervalTrigger(seconds=context.settings.forex_position_check_interval_seconds),
            id="forex_loss_limit_check", max_instances=1, coalesce=True,
        )
        if context.progress_notifier is not None:
            for job_id, hour, minute in (("forex_progress_report_midday", 12, 0), ("forex_progress_report_close", 16, 10)):
                scheduler.add_job(
                    _forex_progress_report_job,
                    CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="America/New_York"),
                    id=job_id, max_instances=1, coalesce=True,
                )

    return scheduler
