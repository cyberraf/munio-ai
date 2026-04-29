"""Multi-source recommendation generation engine.

Sources:
  1. Hotspots (category: layout) — redesign traffic flow at high-incident locations
  2. Temporal patterns (category: scheduling) — adjust operations during spike periods
  3. Robot health (category: maintenance) — battery replacement, sensor calibration
  4. Speed analysis (category: speed_limit) — review speed limits in violation zones

Dedup: checks existing open recommendations by title before inserting.
Each recommendation includes projected savings, implementation cost, and payback period.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from app.db import postgres

logger = logging.getLogger("intelligence.analysis.recommendations")

HOURLY_RATE = 50.0  # $/hour for cost projections

IMPL_COST = {
    "maintenance": 200.0,
    "scheduling": 500.0,
    "layout": 2000.0,
    "speed_limit": 100.0,
}


def _payback_days(impl_cost: float, monthly_savings: float) -> float:
    if monthly_savings <= 0:
        return 9999
    return round(impl_cost / (monthly_savings / 30), 1)


async def generate_recommendations(facility_id: str):
    """Generate recommendations from all analysis results. Runs after all other daily analyses."""
    existing_titles = await postgres.get_open_recommendation_titles(facility_id)
    recs: list[dict] = []

    # ─── 1. Hotspot-based (category: layout) ────────────────────────────────
    hotspots = await postgres.get_hotspots(facility_id)
    for rank, hs in enumerate(sorted(hotspots, key=lambda h: h.get("time_lost_hours", 0), reverse=True)):
        if hs.get("incident_count", 0) < 10:
            continue
        time_saved = (hs.get("time_lost_hours") or 0) * 0.7
        cost_saved = time_saved * HOURLY_RATE
        impl_cost = IMPL_COST["layout"]
        cx, cy = hs.get("center_x", 0), hs.get("center_y", 0)
        radius = hs.get("radius_m", 1.0)
        dominant = hs.get("dominant_type", "PROXIMITY_ALERT")
        title = f"Redesign traffic flow at ({cx:.0f}, {cy:.0f})"

        # Build specific actionable steps based on the dominant incident type
        actions = []
        if "PROXIMITY" in dominant:
            actions = [
                f"Install a physical barrier or guardrail within {radius:.0f}m of ({cx:.1f}, {cy:.1f})",
                "Add a pedestrian crossing signal if human traffic is present",
                f"Create a mandatory slow zone (15 cm/s max) in a {radius*2:.0f}m radius",
                "Consider one-way robot traffic flow to eliminate head-on approaches",
            ]
        elif "SPEED" in dominant:
            actions = [
                f"Reduce the speed limit to 30% of current in the {radius*2:.0f}m zone around ({cx:.1f}, {cy:.1f})",
                "Check for downhill gradient or slippery floor surface",
                "Add speed bumps or floor markings to visually indicate a slow zone",
            ]
        elif "ESTOP" in dominant:
            actions = [
                f"Add convex mirrors at corners near ({cx:.1f}, {cy:.1f}) for sight-line improvement",
                "Install warning lights or audio alerts triggered by approaching robots",
                f"Create a {radius:.0f}m exclusion buffer zone around the collision point",
            ]
        else:
            actions = [
                f"Investigate the area around ({cx:.1f}, {cy:.1f}) for environmental hazards",
                "Review the navigation map for errors in this region",
                "Consider adding additional sensors or markers for better localization",
            ]

        # Visual data for frontend rendering
        visual = {
            "hotspot": {"x": cx, "y": cy, "radius": radius},
            "incident_count": hs.get("incident_count"),
            "dominant_type": dominant,
            "proposed_changes": [],
        }
        if "PROXIMITY" in dominant:
            visual["proposed_changes"] = [
                {"type": "barrier", "x": cx - radius, "y": cy, "x2": cx + radius, "y2": cy, "label": "Physical barrier"},
                {"type": "slow_zone", "x": cx, "y": cy, "radius": radius * 2, "label": f"Slow zone ({radius*2:.0f}m)"},
            ]
        elif "SPEED" in dominant:
            visual["proposed_changes"] = [
                {"type": "slow_zone", "x": cx, "y": cy, "radius": radius * 2, "label": "Reduced speed zone"},
            ]
        else:
            visual["proposed_changes"] = [
                {"type": "exclusion_zone", "x": cx, "y": cy, "radius": radius, "label": "Buffer zone"},
            ]

        recs.append({
            "facility_id": facility_id,
            "category": "layout",
            "priority": rank + 1,
            "title": title,
            "description": (
                f"{hs.get('description', '')}. "
                f"Dominant event type: {dominant.replace('_', ' ').title()}.\n\n"
                f"**Recommended Actions:**\n" +
                "\n".join(f"  {i+1}. {a}" for i, a in enumerate(actions))
            ),
            "evidence": json.dumps({
                "hotspot_id": str(hs.get("id", "")),
                "incident_count": hs.get("incident_count"),
                "time_lost_hours": hs.get("time_lost_hours"),
                "visual": visual,
            }),
            "projected_time_saved_hours": round(time_saved, 2),
            "projected_cost_saved": round(cost_saved, 2),
            "implementation_cost_est": impl_cost,
            "payback_days": _payback_days(impl_cost, cost_saved),
        })

    # ─── 2. Temporal pattern-based (category: scheduling) ───────────────────
    patterns = await postgres.get_patterns(facility_id)
    for pat in patterns:
        if pat.get("pattern_type") != "shift_change":
            continue
        if (pat.get("multiplier") or 0) < 2.0:
            continue

        # Estimate extra incidents during pattern
        baseline = pat.get("baseline_rate", 0)
        actual = pat.get("incident_rate", 0)
        extra_per_day = (actual - baseline) * 3  # 3-hour window
        time_saved = extra_per_day * 30 * 0.8 / 3600 * 30  # 30 days, 80% reduction, convert to hours
        cost_saved = time_saved * HOURLY_RATE
        impl_cost = IMPL_COST["scheduling"]
        hour = pat.get("hour_of_day", 0)
        title = f"Adjust robot operations during {hour:02d}:00 shift change"

        recs.append({
            "facility_id": facility_id,
            "category": "scheduling",
            "priority": 2,
            "title": title,
            "description": (
                f"{pat.get('description', '')}. "
                f"{pat.get('recommendation', '')}"
            ),
            "evidence": json.dumps({
                "pattern_id": str(pat.get("id", "")),
                "multiplier": pat.get("multiplier"),
                "hour": hour,
            }),
            "projected_time_saved_hours": round(time_saved, 2),
            "projected_cost_saved": round(cost_saved, 2),
            "implementation_cost_est": impl_cost,
            "payback_days": _payback_days(impl_cost, cost_saved),
        })

    # ─── 3. Robot health-based (category: maintenance) ──────────────────────
    health_scores = await postgres.get_latest_robot_health(facility_id)
    for h in health_scores:
        flags = h.get("flags") or []
        robot_id = h.get("robot_id", "")

        if "battery_degrading" in flags:
            trend = h.get("battery_trend", 0)
            voltage = h.get("battery_avg_voltage", 0)
            # Project days until critical (varies by robot type)
            critical_v = 6.0 if voltage < 10 else (10.0 if voltage < 15 else 20.0)
            days_until_critical = abs((voltage - critical_v) / max(abs(trend), 0.001))
            title = f"Schedule battery replacement for {robot_id}"
            recs.append({
                "facility_id": facility_id,
                "category": "maintenance",
                "priority": 1 if days_until_critical < 30 else 3,
                "title": title,
                "description": (
                    f"Battery trending down at {trend:.3f} V/day. "
                    f"Current avg: {voltage:.2f}V. "
                    f"Projected {days_until_critical:.0f} days until critical threshold ({critical_v}V)."
                ),
                "evidence": json.dumps({
                    "robot_id": robot_id,
                    "battery_trend": trend,
                    "avg_voltage": voltage,
                    "days_until_critical": round(days_until_critical, 0),
                }),
                "projected_time_saved_hours": 2.0,  # avoid downtime
                "projected_cost_saved": 100.0,
                "implementation_cost_est": IMPL_COST["maintenance"],
                "payback_days": _payback_days(IMPL_COST["maintenance"], 100.0),
            })

        if "sensor_unreliable" in flags or "frequent_hallucinations" in flags:
            reliability = h.get("sensor_reliability_pct", 100)
            hall_count = h.get("hallucination_count", 0)
            title = f"Calibrate/replace sensors on {robot_id}"
            recs.append({
                "facility_id": facility_id,
                "category": "maintenance",
                "priority": 2,
                "title": title,
                "description": (
                    f"Sensor reliability at {reliability:.1f}%. "
                    f"{hall_count} hallucinations detected. "
                    f"Clean or replace ultrasonic/LIDAR sensors."
                ),
                "evidence": json.dumps({
                    "robot_id": robot_id,
                    "sensor_reliability_pct": reliability,
                    "hallucination_count": hall_count,
                }),
                "projected_time_saved_hours": hall_count * 0.5 / 60,
                "projected_cost_saved": hall_count * 0.5,
                "implementation_cost_est": IMPL_COST["maintenance"],
                "payback_days": _payback_days(IMPL_COST["maintenance"], hall_count * 0.5),
            })

        if "high_incident_rate" in flags:
            # Check if root causes are mostly robot_specific
            since_30d = datetime.now(timezone.utc) - timedelta(days=30)
            robot_incs = await postgres.get_incidents(facility_id=facility_id, since=since_30d)
            robot_only = [i for i in robot_incs if i.get("robot_id") == robot_id]
            robot_specific = sum(1 for i in robot_only if i.get("root_cause") == "robot_specific")
            if len(robot_only) > 0 and robot_specific / len(robot_only) > 0.5:
                title = f"Reassign {robot_id} to low-complexity routes"
                recs.append({
                    "facility_id": facility_id,
                    "category": "maintenance",
                    "priority": 2,
                    "title": title,
                    "description": (
                        f"{robot_id} has {len(robot_only)} incidents in 30 days, "
                        f"{robot_specific} attributed to robot-specific causes. "
                        f"Consider reassigning to simpler routes or reducing workload."
                    ),
                    "evidence": json.dumps({
                        "robot_id": robot_id,
                        "total_incidents": len(robot_only),
                        "robot_specific_count": robot_specific,
                    }),
                    "projected_time_saved_hours": len(robot_only) * 30 / 3600 * 0.5,
                    "projected_cost_saved": len(robot_only) * 30 / 3600 * 0.5 * HOURLY_RATE,
                    "implementation_cost_est": 0,
                    "payback_days": 0,
                })

    # ─── 4. Speed limit review (category: speed_limit) ──────────────────────
    since_30d = datetime.now(timezone.utc) - timedelta(days=30)
    all_incidents = await postgres.get_incidents(facility_id=facility_id, since=since_30d)
    speed_violations = [i for i in all_incidents if i.get("event_type") == "SPEED_VIOLATION"]
    if len(speed_violations) > len(all_incidents) * 0.5 and len(all_incidents) > 20:
        title = "Review speed limits — majority of incidents are speed violations"
        recs.append({
            "facility_id": facility_id,
            "category": "speed_limit",
            "priority": 2,
            "title": title,
            "description": (
                f"{len(speed_violations)} of {len(all_incidents)} incidents ({len(speed_violations)/len(all_incidents)*100:.0f}%) "
                f"are speed violations. Speed limits may be set too low, or robots need path optimization."
            ),
            "evidence": json.dumps({
                "speed_violation_count": len(speed_violations),
                "total_incidents": len(all_incidents),
                "percentage": round(len(speed_violations) / len(all_incidents) * 100, 1),
            }),
            "projected_time_saved_hours": len(speed_violations) * 10 / 3600,
            "projected_cost_saved": len(speed_violations) * 10 / 3600 * HOURLY_RATE,
            "implementation_cost_est": IMPL_COST["speed_limit"],
            "payback_days": _payback_days(IMPL_COST["speed_limit"], len(speed_violations) * 10 / 3600 * HOURLY_RATE),
        })

    # ─── Insert (dedup by title) ─────────────────────────────────────────────
    inserted = 0
    for rec in recs:
        if rec["title"] in existing_titles:
            continue
        await postgres.insert_recommendation(rec)
        existing_titles.add(rec["title"])
        inserted += 1

    logger.info(
        f"[{facility_id}] generated {inserted} new recommendations "
        f"({len(recs)} candidates, {len(recs) - inserted} duplicates skipped)"
    )
