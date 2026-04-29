"""APScheduler job registration for batch analysis."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.jobs.hourly import run_hourly_jobs
from app.jobs.daily import run_daily_jobs
from app.jobs.monthly import run_monthly_jobs

logger = logging.getLogger("intelligence.scheduler")

scheduler = AsyncIOScheduler()


def start_scheduler():
    """Register and start all scheduled jobs."""

    if settings.HOURLY_ENABLED:
        scheduler.add_job(
            run_hourly_jobs,
            IntervalTrigger(hours=1),
            id="hourly_batch",
            name="Hourly: clustering, patterns, hallucination detection",
            replace_existing=True,
        )
        logger.info("Registered hourly job")

    if settings.DAILY_ENABLED:
        scheduler.add_job(
            run_daily_jobs,
            CronTrigger(hour=2, minute=0),  # 2:00 AM UTC
            id="daily_batch",
            name="Daily: robot health, productivity, root cause",
            replace_existing=True,
        )
        logger.info("Registered daily job (02:00 UTC)")

    if settings.MONTHLY_ENABLED:
        scheduler.add_job(
            run_monthly_jobs,
            CronTrigger(day=1, hour=6, minute=0),  # 1st of month, 6:00 AM UTC
            id="monthly_batch",
            name="Monthly: report generation",
            replace_existing=True,
        )
        logger.info("Registered monthly job (1st of month, 06:00 UTC)")

    scheduler.start()
    logger.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
