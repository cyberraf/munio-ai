"""Productivity impact calculation — speed-dip based time-lost measurement.

For each incident, queries the ±30s speed profile from ClickHouse to find the
actual slowdown/stop duration (not just a flat estimate). Also detects reroutes
via heading changes.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from app.db import clickhouse, postgres

logger = logging.getLogger("intelligence.analysis.productivity")

COST_PER_ROBOT_HOUR = 45.0  # configurable


async def calculate_time_lost_for_incident(inc: dict) -> float:
    """Measure actual time lost by analyzing the speed profile around an incident.

    1. Get ±30s speed profile from ClickHouse.
    2. Calculate cruise_speed as the 75th percentile.
    3. Find the speed dip: first point where speed < 20% of cruise, last point
       where speed returns to > 80% of cruise.
    4. time_lost = duration of the dip.
    5. If heading changed >30° and returned, add reroute time estimate.
    """
    t = inc["occurred_at"]
    profile = clickhouse.get_speed_profile(inc["robot_id"], t, window_s=30.0)

    if len(profile) < 10:
        return 30.0  # fallback: 30s default

    speeds = [r.get("speed", 0) for r in profile]
    headings = [r.get("heading_deg", 0) for r in profile]
    timestamps = [r.get("timestamp") for r in profile]

    cruise_speed = float(np.percentile([s for s in speeds if s > 0] or [1], 75))
    if cruise_speed < 1:
        return 15.0  # robot was barely moving

    dip_threshold = cruise_speed * 0.2
    recovery_threshold = cruise_speed * 0.8

    # Find dip start and end
    dip_start_idx = None
    dip_end_idx = None
    for i, s in enumerate(speeds):
        if s < dip_threshold and dip_start_idx is None:
            dip_start_idx = i
        if dip_start_idx is not None and s > recovery_threshold:
            dip_end_idx = i
            break

    time_lost = 0.0
    if dip_start_idx is not None and dip_end_idx is not None:
        ts_start = timestamps[dip_start_idx]
        ts_end = timestamps[dip_end_idx]
        if isinstance(ts_start, datetime) and isinstance(ts_end, datetime):
            time_lost = (ts_end - ts_start).total_seconds()
    elif dip_start_idx is not None:
        # Never recovered in the window — estimate from remaining window
        ts_start = timestamps[dip_start_idx]
        ts_last = timestamps[-1]
        if isinstance(ts_start, datetime) and isinstance(ts_last, datetime):
            time_lost = (ts_last - ts_start).total_seconds()

    # Check for heading reroute (>30° deviation and return)
    if len(headings) >= 5:
        # Find max heading delta from the initial heading
        base_heading = headings[0]
        max_delta = max(
            abs((h - base_heading + 180) % 360 - 180) for h in headings
        )
        if max_delta > 30:
            # Estimate reroute adds 50% to time lost (conservative)
            time_lost *= 1.5

    return max(time_lost, 5.0)  # minimum 5s


async def calculate_incident_time_losses(facility_id: str, hours: int = 24):
    """Batch-calculate time_lost_seconds for incidents missing it."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    incidents = await postgres.get_incidents(facility_id=facility_id, since=since)

    updated = 0
    for inc in incidents:
        if inc.get("time_lost_seconds") is not None and inc["time_lost_seconds"] > 0:
            continue
        if inc.get("is_hallucination"):
            # Hallucinations have 0 time lost
            await postgres.update_incident_analysis(
                incident_id=inc["id"],
                root_cause=inc.get("root_cause"),
                confidence=inc.get("root_cause_confidence"),
                time_lost=0,
                is_hallucination=True,
                corroborated=inc.get("corroborated"),
            )
            updated += 1
            continue

        time_lost = await calculate_time_lost_for_incident(inc)
        await postgres.update_incident_analysis(
            incident_id=inc["id"],
            root_cause=inc.get("root_cause"),
            confidence=inc.get("root_cause_confidence"),
            time_lost=round(time_lost, 1),
            is_hallucination=False,
            corroborated=inc.get("corroborated"),
        )
        updated += 1

    logger.info(f"[{facility_id}] calculated time_lost for {updated} incidents")


async def calculate_productivity(facility_id: str, hours: int = 24) -> dict:
    """Full productivity report for a facility."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    incidents = await postgres.get_incidents(facility_id=facility_id, since=since)

    total = len(incidents)
    hallucinations = sum(1 for i in incidents if i.get("is_hallucination"))
    real = total - hallucinations

    time_lost_s = sum(
        i.get("time_lost_seconds") or 30.0
        for i in incidents
        if not i.get("is_hallucination")
    )
    time_lost_hours = time_lost_s / 3600

    # Fleet operating hours: estimate from telemetry event count
    fleet_rates = clickhouse.get_fleet_incident_rates(
        facility_id, since, datetime.now(timezone.utc)
    )
    robot_count = max(len(fleet_rates), 1)
    fleet_operating_hours = robot_count * hours

    fleet_efficiency = max(0, 100 - (time_lost_hours / max(fleet_operating_hours, 1)) * 100)
    cost_estimate = time_lost_hours * COST_PER_ROBOT_HOUR

    # Breakdown by root cause
    cause_counts: dict[str, int] = Counter()
    cause_time: dict[str, float] = defaultdict(float)
    for i in incidents:
        if i.get("is_hallucination"):
            continue
        c = i.get("root_cause") or "unknown"
        cause_counts[c] += 1
        cause_time[c] += (i.get("time_lost_seconds") or 30) / 3600

    by_root_cause = {
        cause: {
            "count": cause_counts[cause],
            "time_lost_hours": round(cause_time[cause], 2),
            "pct": round(cause_counts[cause] / max(real, 1) * 100, 1),
        }
        for cause in sorted(cause_counts, key=cause_counts.get, reverse=True)
    }

    # Breakdown by robot
    robot_counts: dict[str, int] = Counter()
    robot_time: dict[str, float] = defaultdict(float)
    for i in incidents:
        if i.get("is_hallucination"):
            continue
        rid = i.get("robot_id", "unknown")
        robot_counts[rid] += 1
        robot_time[rid] += (i.get("time_lost_seconds") or 30) / 3600

    by_robot = [
        {
            "robot_id": rid,
            "incidents": robot_counts[rid],
            "time_lost_hours": round(robot_time[rid], 2),
        }
        for rid in sorted(robot_counts, key=robot_counts.get, reverse=True)
    ]

    # Breakdown by hotspot
    hotspots = await postgres.get_hotspots(facility_id)
    by_hotspot = [
        {
            "hotspot_id": str(h.get("id", "")),
            "location": f"({h['center_x']:.1f}, {h['center_y']:.1f})",
            "incidents": h.get("incident_count", 0),
            "time_lost_hours": h.get("time_lost_hours", 0),
        }
        for h in hotspots
    ]

    return {
        "facility_id": facility_id,
        "period_hours": hours,
        "total_incidents": total,
        "hallucination_count": hallucinations,
        "real_incident_count": real,
        "total_time_lost_hours": round(time_lost_hours, 2),
        "fleet_operating_hours": round(fleet_operating_hours, 1),
        "fleet_efficiency_pct": round(fleet_efficiency, 1),
        "cost_estimate": round(cost_estimate, 2),
        "cost_per_hour": COST_PER_ROBOT_HOUR,
        "by_root_cause": by_root_cause,
        "by_robot": by_robot,
        "by_hotspot": by_hotspot,
    }
