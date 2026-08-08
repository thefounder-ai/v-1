from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.digest import run_weekly_digest
from app.observability import event_logger, log_event

logger = event_logger("skillorbit.scheduler")
scheduler = AsyncIOScheduler(timezone="UTC")


async def _digest_scheduler_tick() -> None:
    """Daily check: email only users whose 7-day window is due; refresh path on send day if needed."""
    if not settings.digest_configured:
        log_event(logger, logging.INFO, "weekly_digest_skipped", reason="not_configured")
        return
    result = await run_weekly_digest()
    log_event(
        logger,
        logging.INFO,
        "weekly_digest_tick_finished",
        checked_at=datetime.now(timezone.utc).isoformat(),
        **result,
    )


def start_scheduler() -> None:
    if scheduler.running:
        return
    if settings.app_env == "test":
        return
    # Runs daily at 09:00 IST (03:30 UTC) and emails users whose 7-day window is due.
    scheduler.add_job(
        _digest_scheduler_tick,
        trigger="cron",
        hour=3,
        minute=30,
        id="weekly_digest",
        replace_existing=True,
    )
    scheduler.start()
    log_event(logger, logging.INFO, "scheduler_started", weekly_digest_hour_utc=3, weekly_digest_minute_utc=30)


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log_event(logger, logging.INFO, "scheduler_stopped")
