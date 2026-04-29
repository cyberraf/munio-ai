"""Three-condition sensor hallucination detection.

Evaluates PROXIMITY_ALERT and ESTOP_TRIGGERED incidents against:
  1. Sensor recovery — did the reading return to >3x its trigger value within 2s?
  2. Corroboration — did nearby robots also detect a proximity event?
  3. Free-space check — does the facility map show the location as open? (if available)

If all available conditions suggest false detection, marks is_hallucination=true.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.db import clickhouse, postgres

logger = logging.getLogger("intelligence.analysis.hallucination")

CANDIDATE_TYPES = {"PROXIMITY_ALERT", "ESTOP_TRIGGERED", "SENSOR_FAILURE"}


async def _check_sensor_recovery(inc: dict) -> bool | None:
    """Condition 1: Did the sensor reading recover quickly?
    Returns True if it recovered (suggesting hallucination), False if not, None if inconclusive.
    """
    trigger_dist = inc.get("distance_cm", 999)
    if trigger_dist <= 0 or trigger_dist >= 999:
        return None

    t = inc["occurred_at"]
    tel = clickhouse.get_robot_telemetry_window(inc["robot_id"], t, window_s=3.0)

    # Readings after the incident
    after = [
        r for r in tel
        if isinstance(r.get("timestamp"), datetime) and r["timestamp"] > t
    ]
    if len(after) < 3:
        return None  # not enough data

    # Check if distance returned to >3x trigger within the first 2 seconds
    for r in after[:20]:
        dt = (r["timestamp"] - t).total_seconds()
        if dt > 2.0:
            break
        if r.get("distance_cm", 0) > trigger_dist * 3:
            return True  # rapid recovery → likely hallucination

    return False  # did not recover → likely real


async def _check_corroboration(inc: dict) -> bool | None:
    """Condition 2: Did nearby robots also see something?
    Returns True if corroborated (real event), False if not (suggesting hallucination).
    """
    fid = inc.get("facility_id", "")
    t = inc["occurred_at"]
    rid = inc["robot_id"]

    # Get position of the incident robot
    tel = clickhouse.get_robot_telemetry_window(rid, t, window_s=2.0)
    pos = None
    for r in tel:
        px, py = r.get("position_x", 0), r.get("position_y", 0)
        if px != 0 or py != 0:
            pos = (float(px), float(py))
            break

    if pos is None:
        return None  # can't determine position

    nearby = clickhouse.get_nearby_robots(
        facility_id=fid,
        center_time=t,
        center_x=pos[0],
        center_y=pos[1],
        radius_m=5.0,
        window_s=10.0,
        exclude_robot=rid,
    )

    if not nearby:
        return None  # no other robots to corroborate

    # Check if any nearby robot also had a proximity reading
    for nr in nearby:
        if nr.get("min_distance", 999) < 100:
            return True  # corroborated

    return False  # nearby robots saw nothing → suggests hallucination


async def _check_free_space(inc: dict) -> bool | None:
    """Condition 3: Is the incident location known to be free space?
    Returns True if map shows free space (suggesting hallucination), None if no map data.
    """
    # This requires an occupancy grid from facility_maps.
    # For now, return None (inconclusive) — will be implemented when SLAM maps are ingested.
    return None


async def detect_hallucinations(facility_id: str, hours: int = 1):
    """Run hallucination detection on recent incidents.

    For each candidate incident:
      - Evaluate 3 conditions (sensor recovery, corroboration, free space)
      - If majority of available conditions suggest hallucination → flag it
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    incidents = await postgres.get_incidents(facility_id=facility_id, since=start)

    candidates = [
        i for i in incidents
        if i.get("event_type") in CANDIDATE_TYPES
        and i.get("is_hallucination") is None  # not yet analyzed
    ]

    flagged = 0
    for inc in candidates:
        recovery = await _check_sensor_recovery(inc)
        corroboration = await _check_corroboration(inc)
        free_space = await _check_free_space(inc)

        # Score: count conditions suggesting hallucination
        signals = []
        if recovery is True:
            signals.append("recovery")
        if corroboration is False:
            signals.append("no_corroboration")
        if free_space is True:
            signals.append("free_space")

        # Count available (non-None) conditions
        available = sum(1 for x in [recovery, corroboration, free_space] if x is not None)

        # If majority of available conditions suggest hallucination, flag it
        is_hallucination = len(signals) > 0 and len(signals) >= max(1, available / 2)

        # Update incident
        root_cause = "sensor_hallucination" if is_hallucination else inc.get("root_cause")
        confidence = 0.8 if is_hallucination else inc.get("root_cause_confidence")

        await postgres.update_incident_analysis(
            incident_id=inc["id"],
            root_cause=root_cause if is_hallucination else None,  # only set if hallucination
            confidence=confidence if is_hallucination else None,
            time_lost=0 if is_hallucination else None,  # hallucinations have 0 time lost
            is_hallucination=is_hallucination,
            corroborated=corroboration if corroboration is not None else None,
        )
        if is_hallucination:
            flagged += 1

    logger.info(
        f"[{facility_id}] hallucination check: {flagged}/{len(candidates)} flagged "
        f"(of {len(incidents)} total incidents in last {hours}h)"
    )
