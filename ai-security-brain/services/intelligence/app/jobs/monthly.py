"""Monthly batch: full PDF report generation for all facilities."""

import logging
from datetime import datetime, timedelta, timezone

from app.db import postgres
from app.reports.generator import generate_monthly_pdf

logger = logging.getLogger("intelligence.jobs.monthly")


async def run_monthly_jobs():
    """Generate monthly PDF reports for all active facilities."""
    logger.info("=== Starting monthly batch ===")
    try:
        today = datetime.now(timezone.utc)
        first_of_month = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_start = (first_of_month - timedelta(days=1)).replace(day=1)
        month_str = last_month_start.strftime("%Y-%m")

        facilities = await postgres.get_facilities()
        for f in facilities:
            fid = f["id"]
            logger.info(f"Generating monthly report for {fid} ({month_str})")
            try:
                path = await generate_monthly_pdf(fid, month_str)
                if path:
                    logger.info(f"[{fid}] report generated: {path}")
                else:
                    logger.warning(f"[{fid}] report generation returned None")
            except Exception as e:
                logger.error(f"[{fid}] monthly report failed: {e}")
    except Exception as e:
        logger.error(f"Monthly batch failed: {e}")
    logger.info("=== Monthly batch complete ===")
