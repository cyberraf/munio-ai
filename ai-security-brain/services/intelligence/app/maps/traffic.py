"""Traffic density and incident heatmap overlays for facility maps."""

import logging
from datetime import datetime, timedelta

import numpy as np
from scipy.ndimage import gaussian_filter

from app.db import clickhouse, postgres

logger = logging.getLogger("intelligence.maps.traffic")

CELL_SIZE_M = 0.5  # meters per grid cell


def _build_grid(positions: list[tuple[float, float]], bounds: dict) -> dict:
    """Build a 2D histogram from position data.

    Returns {grid: 2d list, min_x, min_y, cell_size, rows, cols}.
    """
    if not positions:
        return {"grid": [], "min_x": 0, "min_y": 0, "cell_size": CELL_SIZE_M, "rows": 0, "cols": 0}

    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]

    min_x = bounds.get("min_x", min(xs))
    max_x = bounds.get("max_x", max(xs))
    min_y = bounds.get("min_y", min(ys))
    max_y = bounds.get("max_y", max(ys))

    cols = max(1, int((max_x - min_x) / CELL_SIZE_M) + 1)
    rows = max(1, int((max_y - min_y) / CELL_SIZE_M) + 1)

    grid = np.zeros((rows, cols), dtype=float)

    for x, y in positions:
        col = min(cols - 1, max(0, int((x - min_x) / CELL_SIZE_M)))
        row = min(rows - 1, max(0, int((y - min_y) / CELL_SIZE_M)))
        grid[row, col] += 1

    # Normalize to 0–1
    max_val = grid.max()
    if max_val > 0:
        grid = grid / max_val

    return {
        "grid": grid.tolist(),
        "min_x": round(min_x, 2),
        "min_y": round(min_y, 2),
        "cell_size": CELL_SIZE_M,
        "rows": rows,
        "cols": cols,
    }


async def calculate_traffic_density(facility_id: str, hours: int = 24) -> dict:
    """2D histogram of robot position frequency.

    Queries ClickHouse for all telemetry positions in the time range,
    builds a grid at 0.5m resolution, normalizes to 0–1.
    """
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)

    telemetry = clickhouse.get_telemetry_range(
        facility_id=facility_id, start=start, end=end, limit=500_000
    )

    positions = [
        (float(t["position_x"]), float(t["position_y"]))
        for t in telemetry
        if t.get("position_x", 0) != 0 or t.get("position_y", 0) != 0
    ]

    logger.info(f"[{facility_id}] traffic density: {len(positions)} position points")

    # Auto-detect bounds from data
    if not positions:
        return {"grid": [], "min_x": 0, "min_y": 0, "cell_size": CELL_SIZE_M, "rows": 0, "cols": 0, "total_points": 0}

    result = _build_grid(positions, {})
    result["total_points"] = len(positions)
    result["period_hours"] = hours
    return result


async def calculate_incident_heatmap(facility_id: str, days: int = 30) -> dict:
    """Gaussian-blurred heatmap of incident positions.

    Queries incidents with positions, builds a density grid,
    applies Gaussian smoothing for visual presentation.
    """
    since = datetime.utcnow() - timedelta(days=days)
    incidents = await postgres.get_incidents(facility_id=facility_id, since=since)

    # Get positions from ClickHouse for each incident
    positions = []
    for inc in incidents:
        t = inc["occurred_at"]
        tel = clickhouse.get_telemetry_range(
            facility_id=facility_id,
            start=t - timedelta(seconds=2),
            end=t + timedelta(seconds=2),
            limit=20,
        )
        for r in tel:
            if r.get("robot_id") == inc["robot_id"]:
                px, py = r.get("position_x", 0), r.get("position_y", 0)
                if px != 0 or py != 0:
                    positions.append((float(px), float(py)))
                    break

    logger.info(f"[{facility_id}] incident heatmap: {len(positions)} positioned incidents")

    if not positions:
        return {"grid": [], "min_x": 0, "min_y": 0, "cell_size": CELL_SIZE_M, "rows": 0, "cols": 0, "total_incidents": 0}

    result = _build_grid(positions, {})

    # Apply Gaussian blur for smooth heatmap
    if result["rows"] > 0 and result["cols"] > 0:
        grid = np.array(result["grid"])
        sigma = max(1, min(result["rows"], result["cols"]) / 10)
        blurred = gaussian_filter(grid, sigma=sigma)
        max_val = blurred.max()
        if max_val > 0:
            blurred = blurred / max_val
        result["grid"] = blurred.tolist()

    result["total_incidents"] = len(positions)
    result["period_days"] = days
    return result
