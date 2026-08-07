from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.digest import run_weekly_digest
from app.observability import event_logger, log_event

logger = event_logger("skillorbit.scheduler")
scheduler = AsyncIOScheduler(timezone="UTC")


async def _daily_digest_tick() -> None:
    """Send weekly learning digests to active learners (once per user per day max)."""
    if not settings.resend_configured:
        log_event(logger, logging.INFO, "daily_digest_skipped", reason="resend_not_configured")
        return
    if not settings.supabase_service_role_key:
        log_event(logger, logging.INFO, "daily_digest_skipped", reason="service_role_not_configured")
        return
    result = await run_weekly_digest()
    log_event(
        logger,
        logging.INFO,
        "daily_digest_finished",
        checked_at=datetime.now(timezone.utc).isoformat(),
        **result,
    )


def start_scheduler() -> None:
    if scheduler.running:
        return
    if settings.app_env == "test":
        return
    scheduler.add_job(
        _daily_digest_tick,
        trigger="cron",
        hour=8,
        minute=0,
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.start()
    log_event(logger, logging.INFO, "scheduler_started")


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log_event(logger, logging.INFO, "scheduler_stopped")
