"""Daily batch: robot health scoring, productivity, root cause attribution."""

import logging

from app.analysis.robot_health import score_robot_health
from app.analysis.productivity import calculate_productivity, calculate_incident_time_losses
from app.analysis.root_cause import attribute_root_causes
from app.analysis.recommendations import generate_recommendations
from app.db import postgres

logger = logging.getLogger("intelligence.jobs.daily")


async def run_daily_jobs():
    """Execute all daily analysis jobs for every active facility."""
    logger.info("=== Starting daily batch ===")
    try:
        facilities = await postgres.get_facilities()
        for f in facilities:
            fid = f["id"]
            logger.info(f"Processing facility: {fid}")
            try:
                await score_robot_health(fid)
            except Exception as e:
                logger.error(f"[{fid}] robot health scoring failed: {e}")
            try:
                await attribute_root_causes(fid, hours=24)
            except Exception as e:
                logger.error(f"[{fid}] root cause attribution failed: {e}")
            try:
                await calculate_incident_time_losses(fid, hours=24)
            except Exception as e:
                logger.error(f"[{fid}] time-loss calculation failed: {e}")
            try:
                metrics = await calculate_productivity(fid, hours=24)
                logger.info(f"[{fid}] productivity: efficiency={metrics['fleet_efficiency_pct']}%, time_lost={metrics['total_time_lost_hours']}h")
            except Exception as e:
                logger.error(f"[{fid}] productivity calculation failed: {e}")
            try:
                await generate_recommendations(fid)
            except Exception as e:
                logger.error(f"[{fid}] recommendation generation failed: {e}")
    except Exception as e:
        logger.error(f"Daily batch failed: {e}")
    logger.info("=== Daily batch complete ===")
