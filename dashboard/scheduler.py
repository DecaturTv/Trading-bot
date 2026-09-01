from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .breakout_loop import (
    breakout_entry_cycle,
    breakout_loss_limit_check_cycle,
    breakout_position_management_cycle,
    breakout_progress_report_cycle,
)
from .context import AppContext
from .forex_loop import (
    forex_entry_cycle,
    forex_loss_limit_check_cycle,
    forex_position_management_cycle,
    forex_progress_report_cycle,
)
from .forex_xsmom_loop import forex_xsmom_rebalance_cycle, forex_xsmom_sync_cycle
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

    async def _forex_xsmom_rebalance_job():
        await forex_xsmom_rebalance_cycle(context, datetime.now(timezone.utc), on_event)

    async def _forex_xsmom_sync_job():
        await forex_xsmom_sync_cycle(context, datetime.now(timezone.utc), on_event)

    async def _breakout_entry_job():
        await breakout_entry_cycle(context, datetime.now(timezone.utc), on_event)

    async def _breakout_entry_job_5m():
        await breakout_entry_cycle(context, datetime.now(timezone.utc), on_event, timeframe="5Min")

    async def _breakout_entry_job_15m():
        await breakout_entry_cycle(context, datetime.now(timezone.utc), on_event, timeframe="15Min")

    async def _breakout_entry_job_1h():
        await breakout_entry_cycle(context, datetime.now(timezone.utc), on_event, timeframe="1Hour")

    async def _breakout_position_job():
        await breakout_position_management_cycle(context, datetime.now(timezone.utc), on_event)

    async def _breakout_loss_limit_job():
        await breakout_loss_limit_check_cycle(context, datetime.now(timezone.utc))

    async def _breakout_progress_report_job():
        await breakout_progress_report_cycle(context, datetime.now(timezone.utc))

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

    # "Breakout Hunter" parallel options strategy (dashboard/breakout_loop.py) —
    # its own cycles / account / positions. No 5Min job: the preset was
    # backtested on 15Min bars, and the 60s 5Min loop fired ~17 single-factor
    # entries in one day (2026-08-31, -$2,159). 15Min + 1H + 1Day only.
    for job, seconds, job_id in (
        (_breakout_entry_job, context.settings.scan_interval_seconds, "breakout_entry_cycle"),
        (_breakout_entry_job_15m, context.settings.intraday_15m_scan_interval_seconds, "breakout_entry_cycle_15m"),
        (_breakout_entry_job_1h, context.settings.intraday_1h_scan_interval_seconds, "breakout_entry_cycle_1h"),
        (_breakout_position_job, context.settings.position_check_interval_seconds, "breakout_position_management_cycle"),
        (_breakout_loss_limit_job, context.settings.position_check_interval_seconds, "breakout_loss_limit_check"),
    ):
        scheduler.add_job(job, IntervalTrigger(seconds=seconds), id=job_id, max_instances=1, coalesce=True)

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
        for job_id, hour, minute in (("breakout_progress_report_midday", 12, 0), ("breakout_progress_report_close", 16, 10)):
            scheduler.add_job(
                _breakout_progress_report_job,
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
        # Cross-sectional 12-month momentum book: check daily whether the
        # quarterly rebalance is due; hourly sync catches legs OANDA closed.
        scheduler.add_job(
            _forex_xsmom_rebalance_job, IntervalTrigger(hours=6),
            id="forex_xsmom_rebalance", max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            _forex_xsmom_sync_job, IntervalTrigger(hours=1),
            id="forex_xsmom_sync", max_instances=1, coalesce=True,
        )
        if context.progress_notifier is not None:
            for job_id, hour, minute in (("forex_progress_report_midday", 12, 0), ("forex_progress_report_close", 16, 10)):
                scheduler.add_job(
                    _forex_progress_report_job,
                    CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone="America/New_York"),
                    id=job_id, max_instances=1, coalesce=True,
                )

    return scheduler
