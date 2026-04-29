"""DBSCAN-based spatial hotspot clustering on incident positions.

Queries incidents from PostgreSQL, enriches with position data from ClickHouse,
runs DBSCAN clustering, and upserts detected hotspots with recommendations.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.cluster import DBSCAN

from app.config import settings
from app.db import clickhouse, postgres

logger = logging.getLogger("intelligence.analysis.spatial")

_RECOMMENDATIONS = {
    "PROXIMITY_ALERT": (
        "Install physical barrier or pedestrian signal near this location. "
        "Consider rerouting robot traffic away from the congested area."
    ),
    "SPEED_VIOLATION": (
        "Reduce the speed limit in this zone. Check for downhill gradient, "
        "slippery surface, or navigation planner overshoot."
    ),
    "ESTOP_TRIGGERED": (
        "Investigate sight lines and blind corners at this location. "
        "Consider adding warning signals, convex mirrors, or a mandatory slow zone."
    ),
    "PATH_DEVIATION": (
        "Review path planning in this area. There may be an obstacle, "
        "map drift, or a floor surface change causing navigation issues."
    ),
    "SENSOR_FAILURE": (
        "Check for environmental factors causing sensor interference: "
        "reflective surfaces, strong lighting, EMI sources, or dust accumulation."
    ),
}
_MIXED_RECOMMENDATION = (
    "Multiple event types suggest a complex intersection or chokepoint. "
    "Consider redesigning traffic flow, widening the passage, or adding scheduling controls."
)


async def detect_hotspots(facility_id: str, days: int = 30):
    """Run DBSCAN on recent incidents to find spatial hotspots.

    Steps:
      1. Fetch incidents from the last N days.
      2. Enrich each incident with position from ClickHouse telemetry.
      3. Filter to incidents with valid (non-zero) positions.
      4. Run DBSCAN (eps=3m, min_samples=5).
      5. Build hotspot records with center, radius, dominant type, time lost, recommendation.
      6. Smart-upsert to PostgreSQL (merge within 2m, resolve stale).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # 1. Fetch incidents
    incidents = await postgres.get_incidents(facility_id=facility_id, since=start)
    if len(incidents) < settings.DBSCAN_MIN_SAMPLES:
        logger.info(f"[{facility_id}] only {len(incidents)} incidents — skipping clustering")
        return []

    # 2. Enrich with position from ClickHouse
    position_map: dict[str, tuple[float, float]] = {}
    for inc in incidents:
        inc_id = str(inc["id"])
        if inc_id in position_map:
            continue
        t = inc["occurred_at"]
        rid = inc["robot_id"]
        telemetry = clickhouse.get_telemetry_range(
            facility_id=facility_id,
            start=t - timedelta(seconds=2),
            end=t + timedelta(seconds=2),
            limit=50,
        )
        best = None
        best_dt = float("inf")
        for tel in telemetry:
            if tel.get("robot_id") != rid:
                continue
            px, py = tel.get("position_x", 0), tel.get("position_y", 0)
            if px == 0 and py == 0:
                continue
            ts = tel.get("timestamp")
            if ts is None:
                continue
            if isinstance(ts, datetime) and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            dt = abs((ts - t).total_seconds()) if isinstance(ts, datetime) else float("inf")
            if dt < best_dt:
                best_dt = dt
                best = (float(px), float(py))
        if best:
            position_map[inc_id] = best

    # 3. Filter to positioned incidents
    positioned = []
    coords_list = []
    for inc in incidents:
        pos = position_map.get(str(inc["id"]))
        if pos and not (pos[0] == 0 and pos[1] == 0):
            positioned.append(inc)
            coords_list.append(pos)

    if len(positioned) < settings.DBSCAN_MIN_SAMPLES:
        logger.info(f"[{facility_id}] only {len(positioned)} positioned — skipping")
        return []

    coords = np.array(coords_list)

    # 4. DBSCAN
    db = DBSCAN(eps=settings.DBSCAN_EPS_M, min_samples=settings.DBSCAN_MIN_SAMPLES)
    labels = db.fit_predict(coords)

    n_clusters = len(set(labels) - {-1})
    logger.info(f"[{facility_id}] DBSCAN: {n_clusters} clusters from {len(coords)} points")

    # 5. Build hotspot records
    hotspots = []
    for label in sorted(set(labels)):
        if label == -1:
            continue
        mask = labels == label
        cluster_coords = coords[mask]
        cluster_incs = [positioned[i] for i in range(len(mask)) if mask[i]]

        cx = float(np.mean(cluster_coords[:, 0]))
        cy = float(np.mean(cluster_coords[:, 1]))
        dists = np.linalg.norm(cluster_coords - np.array([cx, cy]), axis=1)
        radius = float(np.max(dists))
        count = len(cluster_incs)

        type_counts = Counter(i.get("event_type", "UNKNOWN") for i in cluster_incs)
        dominant_type = type_counts.most_common(1)[0][0]

        causes = [i.get("root_cause") for i in cluster_incs if i.get("root_cause")]
        dominant_cause = Counter(causes).most_common(1)[0][0] if causes else None

        time_lost_s = sum(i.get("time_lost_seconds") or 30.0 for i in cluster_incs)
        time_lost_h = time_lost_s / 3600

        times = [i["occurred_at"] for i in cluster_incs if i.get("occurred_at")]
        first = min(times) if times else start
        last = max(times) if times else end
        span = max(1, (last - first).days)

        desc = (
            f"Hotspot at ({cx:.1f}, {cy:.1f}): "
            f"{count} {dominant_type.replace('_', ' ').lower()} events in {span} days"
        )

        if type_counts.most_common(1)[0][1] > count * 0.6:
            rec = _RECOMMENDATIONS.get(dominant_type, _MIXED_RECOMMENDATION)
        else:
            rec = _MIXED_RECOMMENDATION

        hotspots.append({
            "center_x": round(cx, 2),
            "center_y": round(cy, 2),
            "radius_m": round(max(radius, 1.0), 2),
            "incident_count": count,
            "dominant_type": dominant_type,
            "dominant_cause": dominant_cause,
            "time_lost_hours": round(time_lost_h, 2),
            "description": desc,
            "recommendation": rec,
            "projected_savings_hours": round(time_lost_h * 0.7, 2),
            "first_seen": first,
            "last_incident": last,
        })

    # 6. Upsert
    await postgres.upsert_hotspots(facility_id, hotspots)
    logger.info(f"[{facility_id}] upserted {len(hotspots)} hotspots")
    return hotspots
