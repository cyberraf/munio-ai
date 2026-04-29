"""Hourly batch: spatial clustering, temporal patterns, hallucination detection."""

import logging

from app.analysis.spatial import detect_hotspots
from app.analysis.temporal import detect_patterns
from app.analysis.hallucination import detect_hallucinations
from app.db import postgres

logger = logging.getLogger("intelligence.jobs.hourly")


async def run_hourly_jobs():
    """Execute all hourly analysis jobs for every active facility."""
    logger.info("=== Starting hourly batch ===")
    try:
        facilities = await postgres.get_facilities()
        for f in facilities:
            fid = f["id"]
            logger.info(f"Processing facility: {fid}")
            try:
                hotspots = await detect_hotspots(fid, days=30)
                n_hotspots = len(hotspots) if hotspots else 0
            except Exception as e:
                logger.error(f"[{fid}] hotspot detection failed: {e}")
                n_hotspots = 0
            try:
                patterns = await detect_patterns(fid, days=30)
                n_patterns = len(patterns) if patterns else 0
            except Exception as e:
                logger.error(f"[{fid}] temporal pattern detection failed: {e}")
                n_patterns = 0
            logger.info(f"Facility {fid}: {n_hotspots} hotspots, {n_patterns} temporal patterns")
            try:
                await detect_hallucinations(fid, hours=1)
            except Exception as e:
                logger.error(f"[{fid}] hallucination detection failed: {e}")
    except Exception as e:
        logger.error(f"Hourly batch failed: {e}")
    logger.info("=== Hourly batch complete ===")
