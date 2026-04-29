import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

import asyncpg

from app.config import settings

logger = logging.getLogger("intelligence.db.postgres")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.POSTGRES_URL, min_size=2, max_size=10)
        logger.info("PostgreSQL pool created")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ─── Incidents ───────────────────────────────────────────────────────────────

async def get_incidents(
    facility_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 10000,
) -> list[dict]:
    pool = await get_pool()
    conditions = []
    args: list[Any] = []
    idx = 1

    if facility_id:
        conditions.append(f"facility_id = ${idx}")
        args.append(facility_id)
        idx += 1
    if since:
        conditions.append(f"occurred_at >= ${idx}")
        args.append(since)
        idx += 1
    if until:
        conditions.append(f"occurred_at <= ${idx}")
        args.append(until)
        idx += 1

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    q = f"SELECT * FROM incidents{where} ORDER BY occurred_at DESC LIMIT ${idx}"
    args.append(limit)

    rows = await pool.fetch(q, *args)
    return [dict(r) for r in rows]


async def get_incident_count_by_period(
    facility_id: str,
    since: datetime,
    until: datetime | None = None,
) -> int:
    pool = await get_pool()
    if until:
        row = await pool.fetchrow(
            "SELECT count(*) as c FROM incidents WHERE facility_id = $1 AND occurred_at >= $2 AND occurred_at <= $3",
            facility_id, since, until,
        )
    else:
        row = await pool.fetchrow(
            "SELECT count(*) as c FROM incidents WHERE facility_id = $1 AND occurred_at >= $2",
            facility_id, since,
        )
    return row["c"] if row else 0


async def update_incident_analysis(
    incident_id: UUID,
    root_cause: str | None,
    confidence: float | None,
    time_lost: float | None,
    is_hallucination: bool,
    corroborated: bool | None,
):
    pool = await get_pool()
    await pool.execute(
        """UPDATE incidents SET
            root_cause = $2, root_cause_confidence = $3,
            time_lost_seconds = $4, is_hallucination = $5, corroborated = $6
        WHERE id = $1""",
        incident_id, root_cause, confidence, time_lost, is_hallucination, corroborated,
    )


# ─── Hotspots ────────────────────────────────────────────────────────────────

async def upsert_hotspots(facility_id: str, hotspots: list[dict]):
    """Smart upsert: update existing hotspots if center is within 2m, else create new.
    Mark old hotspots as resolved if no longer detected."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Fetch existing active hotspots for distance matching
            existing = await conn.fetch(
                "SELECT id, center_x, center_y FROM hotspots WHERE facility_id = $1 AND status = 'active'",
                facility_id,
            )

            matched_ids = set()
            for h in hotspots:
                # Find closest existing hotspot within 2m
                best_id = None
                best_dist = 2.0  # threshold
                for ex in existing:
                    dx = h["center_x"] - ex["center_x"]
                    dy = h["center_y"] - ex["center_y"]
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_id = ex["id"]

                if best_id:
                    # Update existing hotspot
                    matched_ids.add(best_id)
                    await conn.execute(
                        """UPDATE hotspots SET
                            center_x = $2, center_y = $3, radius_m = $4,
                            incident_count = $5, dominant_type = $6, dominant_cause = $7,
                            time_lost_hours = $8, description = $9, recommendation = $10,
                            last_incident = $11, updated_at = now()
                        WHERE id = $1""",
                        best_id, h["center_x"], h["center_y"], h["radius_m"],
                        h["incident_count"], h.get("dominant_type"), h.get("dominant_cause"),
                        h.get("time_lost_hours"), h.get("description"), h.get("recommendation"),
                        h.get("last_incident"),
                    )
                else:
                    # Create new hotspot
                    await conn.execute(
                        """INSERT INTO hotspots
                        (facility_id, center_x, center_y, radius_m, incident_count,
                         dominant_type, dominant_cause, time_lost_hours, description, recommendation,
                         projected_savings_hours, first_seen, last_incident)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                        facility_id, h["center_x"], h["center_y"], h["radius_m"],
                        h["incident_count"], h.get("dominant_type"), h.get("dominant_cause"),
                        h.get("time_lost_hours"), h.get("description"), h.get("recommendation"),
                        h.get("projected_savings_hours"),
                        h.get("first_seen"), h.get("last_incident"),
                    )

            # Mark unmatched existing hotspots as resolved
            for ex in existing:
                if ex["id"] not in matched_ids:
                    await conn.execute(
                        "UPDATE hotspots SET status = 'resolved', updated_at = now() WHERE id = $1",
                        ex["id"],
                    )


async def get_hotspots(facility_id: str) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM hotspots WHERE facility_id = $1 AND status = 'active' ORDER BY incident_count DESC",
        facility_id,
    )
    return [dict(r) for r in rows]


# ─── Temporal Patterns ───────────────────────────────────────────────────────

async def upsert_patterns(facility_id: str, patterns: list[dict]):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM temporal_patterns WHERE facility_id = $1", facility_id
            )
            for p in patterns:
                await conn.execute(
                    """INSERT INTO temporal_patterns
                    (facility_id, pattern_type, description, hour_of_day, day_of_week,
                     incident_rate, baseline_rate, multiplier, recommendation)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    facility_id, p["pattern_type"], p["description"],
                    p.get("hour_of_day"), p.get("day_of_week"),
                    p.get("incident_rate"), p.get("baseline_rate"),
                    p.get("multiplier"), p.get("recommendation"),
                )


async def get_patterns(facility_id: str) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM temporal_patterns WHERE facility_id = $1 ORDER BY multiplier DESC NULLS LAST",
        facility_id,
    )
    return [dict(r) for r in rows]


# ─── Robot Health ────────────────────────────────────────────────────────────

async def upsert_robot_health(health: dict):
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO robot_health
        (robot_id, facility_id, date, incidents_per_hour, sensor_reliability_pct,
         battery_avg_voltage, battery_trend, behavioral_consistency,
         hallucination_count, overall_score, flags)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT (robot_id, date) DO UPDATE SET
            incidents_per_hour = EXCLUDED.incidents_per_hour,
            sensor_reliability_pct = EXCLUDED.sensor_reliability_pct,
            battery_avg_voltage = EXCLUDED.battery_avg_voltage,
            battery_trend = EXCLUDED.battery_trend,
            behavioral_consistency = EXCLUDED.behavioral_consistency,
            hallucination_count = EXCLUDED.hallucination_count,
            overall_score = EXCLUDED.overall_score,
            flags = EXCLUDED.flags""",
        health["robot_id"], health["facility_id"], health["date"],
        health["incidents_per_hour"], health["sensor_reliability_pct"],
        health["battery_avg_voltage"], health["battery_trend"],
        health["behavioral_consistency"], health["hallucination_count"],
        health["overall_score"], health.get("flags", []),
    )


async def get_robot_health(robot_id: str, days: int = 30) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM robot_health WHERE robot_id = $1 ORDER BY date DESC LIMIT $2",
        robot_id, days,
    )
    return [dict(r) for r in rows]


# ─── Recommendations ────────────────────────────────────────────────────────

async def insert_recommendation(rec: dict):
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO recommendations
        (facility_id, category, priority, title, description, evidence,
         projected_time_saved_hours, projected_cost_saved,
         implementation_cost_est, payback_days)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        rec["facility_id"], rec["category"], rec["priority"],
        rec["title"], rec["description"], rec.get("evidence"),
        rec.get("projected_time_saved_hours"), rec.get("projected_cost_saved"),
        rec.get("implementation_cost_est"), rec.get("payback_days"),
    )


async def get_recommendations(facility_id: str, status: str = "open") -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM recommendations WHERE facility_id = $1 AND status = $2 ORDER BY priority",
        facility_id, status,
    )
    return [dict(r) for r in rows]


async def get_open_recommendation_titles(facility_id: str) -> set[str]:
    """Get titles of all open recommendations for dedup check."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT title FROM recommendations WHERE facility_id = $1 AND status = 'open'",
        facility_id,
    )
    return {r["title"] for r in rows}


async def update_recommendation_status(rec_id: str, status: str):
    """Update recommendation status (open, implemented, dismissed)."""
    pool = await get_pool()
    if status == "implemented":
        await pool.execute(
            "UPDATE recommendations SET status = $2, implemented_at = now() WHERE id = $1",
            rec_id, status,
        )
    else:
        await pool.execute(
            "UPDATE recommendations SET status = $2 WHERE id = $1",
            rec_id, status,
        )


async def get_latest_robot_health(facility_id: str) -> list[dict]:
    """Get the most recent health score per robot for a facility."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT DISTINCT ON (robot_id) *
        FROM robot_health WHERE facility_id = $1
        ORDER BY robot_id, date DESC""",
        facility_id,
    )
    return [dict(r) for r in rows]


async def get_robot_health_history(robot_id: str, days: int = 30) -> list[dict]:
    """Get health score history for a single robot."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM robot_health WHERE robot_id = $1 ORDER BY date DESC LIMIT $2",
        robot_id, days,
    )
    return [dict(r) for r in rows]


# ─── Facilities ──────────────────────────────────────────────────────────────

async def get_nearest_hotspot(facility_id: str, x: float, y: float, max_dist: float = 5.0) -> dict | None:
    """Find the nearest active hotspot within max_dist meters of a position."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT *, sqrt(pow(center_x - $2, 2) + pow(center_y - $3, 2)) AS dist
        FROM hotspots
        WHERE facility_id = $1 AND status = 'active'
        ORDER BY dist LIMIT 1""",
        facility_id, x, y,
    )
    if rows and rows[0]["dist"] <= max_dist:
        return dict(rows[0])
    return None


async def get_pattern_for_hour(facility_id: str, hour: int) -> dict | None:
    """Get active temporal pattern matching a specific hour."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """SELECT * FROM temporal_patterns
        WHERE facility_id = $1
          AND (hour_of_day = $2 OR hour_of_day = $2 - 1 OR hour_of_day = $2 + 1)
        ORDER BY multiplier DESC NULLS LAST LIMIT 1""",
        facility_id, hour,
    )
    return dict(row) if row else None


async def get_recent_map_changes(facility_id: str, x: float, y: float, radius: float = 3.0) -> list[dict]:
    """Find recent map changes near a position."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT * FROM map_changes
        WHERE facility_id = $1
          AND sqrt(pow(location_x - $2, 2) + pow(location_y - $3, 2)) < $4
          AND detected_at > now() - interval '30 days'
        ORDER BY detected_at DESC LIMIT 5""",
        facility_id, x, y, radius,
    )
    return [dict(r) for r in rows]


async def get_robot_incident_counts(facility_id: str, since: datetime) -> dict[str, int]:
    """Get incident count per robot for fleet comparison."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT robot_id, count(*) as cnt FROM incidents WHERE facility_id = $1 AND occurred_at >= $2 GROUP BY robot_id",
        facility_id, since,
    )
    return {r["robot_id"]: r["cnt"] for r in rows}


async def get_facilities() -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch("SELECT id, name FROM facilities ORDER BY name")
    return [dict(r) for r in rows]


# ─── Monthly Reports ────────────────────────────────────────────────────────

async def upsert_monthly_report(report: dict):
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO monthly_reports
        (facility_id, month, total_incidents, total_time_lost_hours,
         fleet_efficiency_pct, hotspot_count, recommendations_count, report_url)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (facility_id, month) DO UPDATE SET
            total_incidents = EXCLUDED.total_incidents,
            total_time_lost_hours = EXCLUDED.total_time_lost_hours,
            fleet_efficiency_pct = EXCLUDED.fleet_efficiency_pct,
            hotspot_count = EXCLUDED.hotspot_count,
            recommendations_count = EXCLUDED.recommendations_count,
            report_url = EXCLUDED.report_url,
            generated_at = now()""",
        report["facility_id"], report["month"],
        report["total_incidents"], report["total_time_lost_hours"],
        report["fleet_efficiency_pct"], report["hotspot_count"],
        report["recommendations_count"], report.get("report_url"),
    )


async def get_monthly_reports(facility_id: str) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM monthly_reports WHERE facility_id = $1 ORDER BY month DESC",
        facility_id,
    )
    return [dict(r) for r in rows]


# ─── Facility Maps ──────────────────────────────────────────────────────────

async def insert_facility_map(m: dict) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        """INSERT INTO facility_maps
        (facility_id, map_type, source, resolution, origin_x, origin_y,
         width_pixels, height_pixels, s3_url, is_active, captured_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        RETURNING id""",
        m["facility_id"], m["map_type"], m.get("source"), m.get("resolution"),
        m.get("origin_x"), m.get("origin_y"), m.get("width_pixels"), m.get("height_pixels"),
        m["s3_url"], m.get("is_active", True), m.get("captured_at"),
    )
    return str(row["id"])


async def deactivate_facility_maps(facility_id: str, except_id: str | None = None):
    pool = await get_pool()
    if except_id:
        await pool.execute(
            "UPDATE facility_maps SET is_active = false WHERE facility_id = $1 AND id != $2",
            facility_id, except_id,
        )
    else:
        await pool.execute(
            "UPDATE facility_maps SET is_active = false WHERE facility_id = $1",
            facility_id,
        )


async def get_active_map(facility_id: str) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM facility_maps WHERE facility_id = $1 AND is_active = true ORDER BY created_at DESC LIMIT 1",
        facility_id,
    )
    return dict(row) if row else None


async def get_previous_map(facility_id: str, current_id: str) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM facility_maps WHERE facility_id = $1 AND id != $2 ORDER BY created_at DESC LIMIT 1",
        facility_id, current_id,
    )
    return dict(row) if row else None


async def get_facility_maps(facility_id: str) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM facility_maps WHERE facility_id = $1 ORDER BY created_at DESC",
        facility_id,
    )
    return [dict(r) for r in rows]


# ─── Map Changes ────────────────────────────────────────────────────────────

async def insert_map_change(c: dict):
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO map_changes
        (facility_id, old_map_id, new_map_id, change_type, location_x, location_y,
         description, impact)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        c["facility_id"], c.get("old_map_id"), c.get("new_map_id"),
        c.get("change_type"), c.get("location_x"), c.get("location_y"),
        c.get("description"), c.get("impact"),
    )


async def get_map_changes(facility_id: str, limit: int = 50) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM map_changes WHERE facility_id = $1 ORDER BY detected_at DESC LIMIT $2",
        facility_id, limit,
    )
    return [dict(r) for r in rows]
