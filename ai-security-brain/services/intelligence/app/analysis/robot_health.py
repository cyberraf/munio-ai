"""Daily robot health scoring — composite metric per robot per day.

For each active robot:
  - incidents_per_hour from PostgreSQL
  - sensor_reliability_pct from ClickHouse (valid readings / total)
  - battery_avg_voltage + battery_trend (linear regression over 30 days)
  - behavioral_consistency (speed CV vs 30-day baseline)
  - hallucination_count from PostgreSQL
  - overall_score: weighted composite (30% incidents, 25% sensor, 20% battery, 15% behavior, 10% hallucination)
  - flags: notable conditions array
"""

import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np

from app.db import clickhouse, postgres

logger = logging.getLogger("intelligence.analysis.robot_health")


def _battery_trend_slope(daily_avgs: list[tuple[datetime, float]]) -> float:
    """Linear regression slope of daily battery voltage. Negative = degrading."""
    if len(daily_avgs) < 3:
        return 0.0
    days = np.array([(d[0] - daily_avgs[0][0]).days if isinstance(d[0], (date, datetime)) else i
                      for i, d in enumerate(daily_avgs)], dtype=float)
    voltages = np.array([d[1] for d in daily_avgs], dtype=float)
    if len(days) < 3 or np.std(days) == 0:
        return 0.0
    # y = mx + b → m = slope (V/day)
    m = float(np.polyfit(days, voltages, 1)[0])
    return m


def _battery_health_score(avg_voltage: float, trend: float, robot_type: str = "") -> float:
    """Score 0-100 based on absolute voltage and trend."""
    # Absolute voltage score (varies by robot type, use generic thresholds)
    if avg_voltage <= 0:
        return 50.0  # no data
    if avg_voltage > 12:
        # Large battery (Locus, MiR) — 25V nominal, 20V critical
        abs_score = min(100, max(0, (avg_voltage - 20) / 5 * 100))
    elif avg_voltage > 9:
        # Medium battery (TurtleBot) — 12V nominal, 10V critical
        abs_score = min(100, max(0, (avg_voltage - 10) / 2.6 * 100))
    else:
        # Small battery (PiCar-X) — 7.4V nominal, 6V critical
        abs_score = min(100, max(0, (avg_voltage - 6) / 1.4 * 100))

    # Trend penalty: -0.01 V/day = -10 points, -0.05 V/day = -50 points
    trend_penalty = max(0, -trend * 1000)  # scale: 0.01 V/day = 10 points
    return max(0, min(100, abs_score - trend_penalty))


async def score_robot_health(facility_id: str, target_date: date | None = None):
    """Calculate health scores for all active robots in a facility."""
    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # Get active robots from ClickHouse
    active_robots = clickhouse.get_robots_active_today(facility_id, day_start, day_end)
    if not active_robots:
        logger.info(f"[{facility_id}] no active robots on {target_date}")
        return

    # Get all incidents for the day for fleet average comparison
    all_incidents = await postgres.get_incidents(
        facility_id=facility_id, since=day_start, until=day_end
    )
    fleet_incident_count = len(all_incidents)
    fleet_avg_per_robot = fleet_incident_count / max(len(active_robots), 1)

    # Get 30-day speed baseline per robot for behavioral consistency
    baseline_start = day_start - timedelta(days=30)

    scored = 0
    for robot_id in active_robots:
        # ─── Incidents per hour ──────────────────────────────────────────
        robot_incidents = [i for i in all_incidents if i.get("robot_id") == robot_id]
        operating_hours = 24.0  # assume full day (could be refined with actual online time)
        incidents_per_hour = len(robot_incidents) / max(operating_hours, 1)

        # ─── Sensor reliability ──────────────────────────────────────────
        sensor_stats = clickhouse.get_robot_sensor_stats(robot_id, day_start, day_end)
        total = sensor_stats["total"]
        if total > 0:
            # Both distance and battery must be valid for a "valid" reading
            valid = min(sensor_stats["valid_distance"], sensor_stats["valid_battery"])
            sensor_reliability = (valid / total) * 100
        else:
            sensor_reliability = 100.0  # no data = assume OK

        # ─── Battery ────────────────────────────────────────────────────
        battery_today = clickhouse.get_battery_stats(robot_id, day_start, day_end)
        avg_voltage = battery_today["avg_voltage"]

        daily_avgs = clickhouse.get_daily_battery_avg(robot_id, days=30)
        battery_trend = _battery_trend_slope(daily_avgs)

        battery_score = _battery_health_score(avg_voltage, battery_trend)

        # ─── Behavioral consistency ──────────────────────────────────────
        today_speed = clickhouse.get_robot_speed_stats(robot_id, day_start, day_end)
        baseline_speed = clickhouse.get_robot_speed_stats(robot_id, baseline_start, day_start)

        if today_speed["count"] > 10 and baseline_speed["count"] > 100:
            # Compare coefficient of variation (CV = std/mean)
            today_cv = today_speed["std_speed"] / max(today_speed["avg_speed"], 0.01)
            baseline_cv = baseline_speed["std_speed"] / max(baseline_speed["avg_speed"], 0.01)

            # Compare stop frequency
            today_stop_pct = today_speed["stops"] / max(today_speed["count"], 1)
            baseline_stop_pct = baseline_speed["stops"] / max(baseline_speed["count"], 1)

            # Deviation from baseline (0 = identical, higher = more different)
            cv_deviation = abs(today_cv - baseline_cv) / max(baseline_cv, 0.01)
            stop_deviation = abs(today_stop_pct - baseline_stop_pct) / max(baseline_stop_pct, 0.01)

            behavioral_consistency = max(0, 100 - (cv_deviation * 30 + stop_deviation * 20))
        else:
            behavioral_consistency = 100.0  # not enough data

        # ─── Hallucinations ──────────────────────────────────────────────
        hallucination_count = sum(
            1 for i in robot_incidents if i.get("is_hallucination")
        )

        # ─── Overall score (weighted composite) ──────────────────────────
        incident_score = max(0, 100 - incidents_per_hour * 50)  # 2 inc/hr = 0
        hallucination_score = max(0, 100 - hallucination_count * 25)  # 4 = 0

        overall_score = (
            incident_score * 0.30
            + sensor_reliability * 0.25
            + battery_score * 0.20
            + behavioral_consistency * 0.15
            + hallucination_score * 0.10
        )

        # ─── Flags ───────────────────────────────────────────────────────
        flags: list[str] = []
        if fleet_avg_per_robot > 0 and len(robot_incidents) > fleet_avg_per_robot * 2.5:
            flags.append("high_incident_rate")
        if battery_trend < -0.01:
            flags.append("battery_degrading")
        if sensor_reliability < 95:
            flags.append("sensor_unreliable")
        if hallucination_count > 3:
            flags.append("frequent_hallucinations")
        if behavioral_consistency < 70:
            flags.append("behavioral_anomaly")

        # ─── Upsert ─────────────────────────────────────────────────────
        await postgres.upsert_robot_health({
            "robot_id": robot_id,
            "facility_id": facility_id,
            "date": target_date,
            "incidents_per_hour": round(incidents_per_hour, 3),
            "sensor_reliability_pct": round(sensor_reliability, 1),
            "battery_avg_voltage": round(avg_voltage, 2),
            "battery_trend": round(battery_trend, 4),
            "behavioral_consistency": round(behavioral_consistency, 1),
            "hallucination_count": hallucination_count,
            "overall_score": round(overall_score, 1),
            "flags": flags,
        })
        scored += 1

    logger.info(
        f"[{facility_id}] scored health for {scored} robots on {target_date}"
    )
