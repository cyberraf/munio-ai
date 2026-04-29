import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import clickhouse_connect

from app.config import settings

logger = logging.getLogger("intelligence.db.clickhouse")

_client = None


def _ensure_tz(rows: list[dict]) -> list[dict]:
    """Make any naive datetime values timezone-aware (UTC)."""
    for row in rows:
        for k, v in row.items():
            if isinstance(v, datetime) and v.tzinfo is None:
                row[k] = v.replace(tzinfo=timezone.utc)
    return rows


def get_client():
    global _client
    if _client is None:
        parsed = urlparse(settings.CLICKHOUSE_URL)
        host = parsed.hostname or "localhost"
        # clickhouse_connect uses HTTP interface (default 8123), not native 9000
        port = 8123
        database = (parsed.path or "/default").lstrip("/") or "default"
        username = parsed.username or "default"
        password = parsed.password or "asb_dev"
        _client = clickhouse_connect.get_client(
            host=host, port=port, database=database,
            username=username, password=password,
        )
        logger.info(f"ClickHouse client connected to {host}:{port}/{database}")
    return _client


def get_telemetry_range(
    facility_id: str | None,
    start: datetime,
    end: datetime,
    limit: int = 100_000,
) -> list[dict]:
    """Fetch telemetry events from ClickHouse for analysis."""
    client = get_client()
    conditions = ["timestamp >= %(start)s", "timestamp <= %(end)s"]
    params: dict = {"start": start, "end": end, "limit": limit}

    if facility_id:
        conditions.append("facility_id = %(facility_id)s")
        params["facility_id"] = facility_id

    where = " AND ".join(conditions)
    q = f"""SELECT robot_id, timestamp, distance_cm, speed, steering_angle,
        battery_voltage, status, position_x, position_y, heading_deg,
        robot_type, vendor, facility_id
    FROM telemetry WHERE {where}
    ORDER BY timestamp LIMIT %(limit)s"""

    result = client.query(q, parameters=params)
    columns = result.column_names
    return _ensure_tz([dict(zip(columns, row)) for row in result.result_rows])


def get_incident_positions(
    facility_id: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Get position data around incident times for spatial clustering."""
    client = get_client()
    q = """SELECT robot_id, timestamp, position_x, position_y, distance_cm, speed
    FROM telemetry
    WHERE facility_id = %(facility_id)s
      AND timestamp >= %(start)s AND timestamp <= %(end)s
      AND (distance_cm > 0 AND distance_cm < 200)
    ORDER BY timestamp"""

    result = client.query(q, parameters={
        "facility_id": facility_id, "start": start, "end": end,
    })
    columns = result.column_names
    return _ensure_tz([dict(zip(columns, row)) for row in result.result_rows])


def get_robot_telemetry_window(
    robot_id: str,
    center: datetime,
    window_s: float = 3.0,
) -> list[dict]:
    """Get telemetry for a specific robot in a time window around a point."""
    client = get_client()
    start = center - timedelta(seconds=window_s)
    end = center + timedelta(seconds=window_s)
    q = """SELECT robot_id, timestamp, distance_cm, speed, position_x, position_y,
        heading_deg, status
    FROM telemetry
    WHERE robot_id = %(robot_id)s AND timestamp >= %(start)s AND timestamp <= %(end)s
    ORDER BY timestamp"""
    result = client.query(q, parameters={"robot_id": robot_id, "start": start, "end": end})
    columns = result.column_names
    return _ensure_tz([dict(zip(columns, row)) for row in result.result_rows])


def get_nearby_robots(
    facility_id: str,
    center_time: datetime,
    center_x: float,
    center_y: float,
    radius_m: float = 5.0,
    window_s: float = 10.0,
    exclude_robot: str = "",
) -> list[dict]:
    """Find robots within radius_m of a position at a given time."""
    client = get_client()
    start = center_time - timedelta(seconds=window_s)
    end = center_time + timedelta(seconds=window_s)
    q = """SELECT DISTINCT robot_id, min(distance_cm) as min_distance,
        avg(position_x) as avg_x, avg(position_y) as avg_y
    FROM telemetry
    WHERE facility_id = %(facility_id)s
      AND timestamp >= %(start)s AND timestamp <= %(end)s
      AND robot_id != %(exclude)s
      AND position_x != 0 AND position_y != 0
    GROUP BY robot_id
    HAVING sqrt(pow(avg(position_x) - %(cx)s, 2) + pow(avg(position_y) - %(cy)s, 2)) < %(radius)s"""
    result = client.query(q, parameters={
        "facility_id": facility_id, "start": start, "end": end,
        "exclude": exclude_robot, "cx": center_x, "cy": center_y, "radius": radius_m,
    })
    columns = result.column_names
    return _ensure_tz([dict(zip(columns, row)) for row in result.result_rows])


def get_speed_profile(
    robot_id: str,
    center: datetime,
    window_s: float = 30.0,
) -> list[dict]:
    """Get speed readings around an incident for productivity analysis."""
    client = get_client()
    start = center - timedelta(seconds=window_s)
    end = center + timedelta(seconds=window_s)
    q = """SELECT timestamp, speed, heading_deg, position_x, position_y
    FROM telemetry
    WHERE robot_id = %(robot_id)s AND timestamp >= %(start)s AND timestamp <= %(end)s
    ORDER BY timestamp"""
    result = client.query(q, parameters={"robot_id": robot_id, "start": start, "end": end})
    columns = result.column_names
    return _ensure_tz([dict(zip(columns, row)) for row in result.result_rows])


def get_fleet_incident_rates(
    facility_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, float]:
    """Get per-robot event count for fleet comparison."""
    client = get_client()
    # Count telemetry events per robot as a proxy for operating time
    q = """SELECT robot_id, count() as events
    FROM telemetry
    WHERE facility_id = %(facility_id)s AND timestamp >= %(start)s AND timestamp <= %(end)s
    GROUP BY robot_id"""
    result = client.query(q, parameters={"facility_id": facility_id, "start": start, "end": end})
    return {row[0]: row[1] for row in result.result_rows}


def get_battery_stats(robot_id: str, start: datetime, end: datetime) -> dict:
    """Get battery statistics for a robot over a period."""
    client = get_client()
    q = """SELECT
        avg(battery_voltage) as avg_voltage,
        min(battery_voltage) as min_voltage,
        max(battery_voltage) as max_voltage
    FROM telemetry
    WHERE robot_id = %(robot_id)s
      AND timestamp >= %(start)s AND timestamp <= %(end)s
      AND battery_voltage > 0"""

    result = client.query(q, parameters={
        "robot_id": robot_id, "start": start, "end": end,
    })
    if result.result_rows:
        row = result.result_rows[0]
        return {"avg_voltage": row[0], "min_voltage": row[1], "max_voltage": row[2]}
    return {"avg_voltage": 0, "min_voltage": 0, "max_voltage": 0}


def get_daily_battery_avg(robot_id: str, days: int = 30) -> list[tuple[datetime, float]]:
    """Get daily average battery voltage for battery trend calculation."""
    client = get_client()
    start = datetime.now(timezone.utc) - timedelta(days=days)
    q = """SELECT toDate(timestamp) as day, avg(battery_voltage) as avg_v
    FROM telemetry
    WHERE robot_id = %(robot_id)s AND timestamp >= %(start)s AND battery_voltage > 0
    GROUP BY day ORDER BY day"""
    result = client.query(q, parameters={"robot_id": robot_id, "start": start})
    return [(row[0], float(row[1])) for row in result.result_rows]


def get_robot_sensor_stats(robot_id: str, start: datetime, end: datetime) -> dict:
    """Get sensor validity statistics for a robot."""
    client = get_client()
    q = """SELECT
        count() as total,
        countIf(distance_cm > 0) as valid_distance,
        countIf(battery_voltage > 0) as valid_battery
    FROM telemetry
    WHERE robot_id = %(robot_id)s AND timestamp >= %(start)s AND timestamp <= %(end)s"""
    result = client.query(q, parameters={"robot_id": robot_id, "start": start, "end": end})
    if result.result_rows:
        r = result.result_rows[0]
        return {"total": r[0], "valid_distance": r[1], "valid_battery": r[2]}
    return {"total": 0, "valid_distance": 0, "valid_battery": 0}


def get_robot_speed_stats(robot_id: str, start: datetime, end: datetime) -> dict:
    """Get speed distribution stats for behavioral consistency."""
    client = get_client()
    q = """SELECT
        avg(speed) as avg_speed,
        stddevPop(speed) as std_speed,
        count() as cnt,
        countIf(speed = 0) as stops
    FROM telemetry
    WHERE robot_id = %(robot_id)s AND timestamp >= %(start)s AND timestamp <= %(end)s"""
    result = client.query(q, parameters={"robot_id": robot_id, "start": start, "end": end})
    if result.result_rows:
        r = result.result_rows[0]
        return {"avg_speed": float(r[0] or 0), "std_speed": float(r[1] or 0),
                "count": int(r[2]), "stops": int(r[3])}
    return {"avg_speed": 0, "std_speed": 0, "count": 0, "stops": 0}


def get_robots_active_today(facility_id: str, start: datetime, end: datetime) -> list[str]:
    """Get list of robot IDs that sent telemetry during the period."""
    client = get_client()
    q = """SELECT DISTINCT robot_id FROM telemetry
    WHERE facility_id = %(facility_id)s AND timestamp >= %(start)s AND timestamp <= %(end)s"""
    result = client.query(q, parameters={"facility_id": facility_id, "start": start, "end": end})
    return [row[0] for row in result.result_rows]
