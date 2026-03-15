"""Scheduler construction for recurring verification retries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


def build_scheduler(job: Callable[[], Awaitable[None]], interval_hours: int) -> AsyncIOScheduler:
    """Create the APScheduler instance used by the running bot.

    The first retry is intentionally delayed by one minute so startup can finish
    and slash commands can sync before the first bulk member scan begins.
    """

    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    scheduler.add_job(
        job,
        trigger=IntervalTrigger(hours=interval_hours),
        id="daily-verification-retry",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    return scheduler
