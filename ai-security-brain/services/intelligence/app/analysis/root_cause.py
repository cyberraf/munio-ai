"""Context-aware root cause attribution for safety incidents.

Evaluates each incident against spatial, temporal, fleet, and sensor context
to assign a root cause with confidence score. Evaluated in priority order:
  1. sensor_hallucination — sensor spike not corroborated, recovered fast
  2. facility_design — incident inside a known hotspot with 10+ events
  3. human_traffic — occurred during a detected shift-change pattern
  4. robot_specific — robot's incident rate > 2.5x fleet average
  5. environmental — recent map change nearby
  6. normal_operation — system worked correctly (incident prevented)
  7. unknown — no context matched
"""

import logging
from datetime import datetime, timedelta, timezone

from app.db import clickhouse, postgres

logger = logging.getLogger("intelligence.analysis.root_cause")


async def _get_incident_position(inc: dict) -> tuple[float, float] | None:
    """Get the robot's position at incident time from ClickHouse."""
    t = inc["occurred_at"]
    tel = clickhouse.get_robot_telemetry_window(inc["robot_id"], t, window_s=2)
    for r in tel:
        px, py = r.get("position_x", 0), r.get("position_y", 0)
        if px != 0 or py != 0:
            return (float(px), float(py))
    return None


async def _check_sensor_recovery(inc: dict) -> bool:
    """Did the triggering reading return to >3x its value within 2 seconds?"""
    if inc.get("event_type") not in ("PROXIMITY_ALERT", "SENSOR_FAILURE", "ESTOP_TRIGGERED"):
        return False
    t = inc["occurred_at"]
    tel = clickhouse.get_robot_telemetry_window(inc["robot_id"], t, window_s=3)
    trigger_dist = inc.get("distance_cm", 999)
    if trigger_dist <= 0 or trigger_dist >= 999:
        return False
    # Look for readings after the incident that are >3x the trigger value
    after = [r for r in tel if isinstance(r.get("timestamp"), datetime) and r["timestamp"] > t]
    recovered = any(r.get("distance_cm", 0) > trigger_dist * 3 for r in after[:20])
    return recovered


async def _check_corroboration(inc: dict, pos: tuple[float, float] | None) -> bool:
    """Were nearby robots also detecting proximity events?"""
    if pos is None:
        return True  # can't check, assume corroborated
    nearby = clickhouse.get_nearby_robots(
        facility_id=inc.get("facility_id", ""),
        center_time=inc["occurred_at"],
        center_x=pos[0],
        center_y=pos[1],
        radius_m=5.0,
        window_s=10.0,
        exclude_robot=inc["robot_id"],
    )
    if not nearby:
        return False  # no other robots nearby to corroborate
    # Check if any nearby robot also had a low distance reading
    for nr in nearby:
        if nr.get("min_distance", 999) < 100:
            return True
    return False


async def attribute_single(inc: dict) -> tuple[str, float]:
    """Attribute root cause for a single incident. Returns (cause, confidence)."""
    fid = inc.get("facility_id", "")
    t = inc["occurred_at"]
    pos = await _get_incident_position(inc)

    # 1. Sensor hallucination
    sensor_recovered = await _check_sensor_recovery(inc)
    corroborated = await _check_corroboration(inc, pos)
    if sensor_recovered and not corroborated:
        return "sensor_hallucination", 0.8

    # 2. Facility design (inside a high-count hotspot)
    if pos:
        hotspot = await postgres.get_nearest_hotspot(fid, pos[0], pos[1], max_dist=5.0)
        if hotspot and hotspot.get("incident_count", 0) >= 10:
            return "facility_design", 0.85

    # 3. Human traffic (during shift-change pattern)
    if isinstance(t, datetime):
        pattern = await postgres.get_pattern_for_hour(fid, t.hour)
        if pattern and pattern.get("pattern_type") == "shift_change":
            return "human_traffic", 0.75

    # 4. Robot-specific (robot has >2.5x fleet average incident rate)
    since_30d = datetime.now(timezone.utc) - timedelta(days=30)
    robot_counts = await postgres.get_robot_incident_counts(fid, since_30d)
    if robot_counts:
        avg_count = sum(robot_counts.values()) / max(len(robot_counts), 1)
        robot_count = robot_counts.get(inc["robot_id"], 0)
        if avg_count > 0 and robot_count > avg_count * 2.5:
            return "robot_specific", 0.7

    # 5. Environmental (recent map change nearby)
    if pos:
        changes = await postgres.get_recent_map_changes(fid, pos[0], pos[1], radius=3.0)
        if changes:
            return "environmental", 0.65

    # 6. Normal operation (incident was prevented — the system worked)
    if inc.get("is_prevented", True):
        return "normal_operation", 0.6

    # 7. Unknown
    return "unknown", 0.3


async def attribute_root_causes(facility_id: str, hours: int = 24):
    """Batch-assign root causes to recent unattributed incidents."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    incidents = await postgres.get_incidents(facility_id=facility_id, since=since)

    attributed = 0
    for inc in incidents:
        if inc.get("root_cause"):
            continue

        cause, confidence = await attribute_single(inc)

        # Determine corroboration
        pos = await _get_incident_position(inc)
        corr = await _check_corroboration(inc, pos)
        is_hall = cause == "sensor_hallucination"

        await postgres.update_incident_analysis(
            incident_id=inc["id"],
            root_cause=cause,
            confidence=confidence,
            time_lost=inc.get("time_lost_seconds") or 30.0,
            is_hallucination=is_hall,
            corroborated=corr,
        )
        attributed += 1

    logger.info(f"[{facility_id}] attributed root causes to {attributed}/{len(incidents)} incidents")
