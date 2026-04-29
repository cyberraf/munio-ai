from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import postgres
from app.maps.slam import ingest_slam_map, ingest_ros2_occupancy_grid, upload_floor_plan
from app.maps.traffic import calculate_traffic_density, calculate_incident_heatmap

router = APIRouter(prefix="/maps", tags=["maps"])

RANGE_MAP = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}


@router.get("/facility/{facility_id}/current")
async def get_current_map(facility_id: str):
    """Get the active map metadata + image URL for a facility."""
    m = await postgres.get_active_map(facility_id)
    if not m:
        return JSONResponse(status_code=404, content={"error": "no active map"})
    return m


@router.get("/facility/{facility_id}/all")
async def list_maps(facility_id: str):
    """List all maps for a facility."""
    return await postgres.get_facility_maps(facility_id)


@router.get("/facility/{facility_id}/changes")
async def get_map_changes(facility_id: str, limit: int = Query(50)):
    """List detected map changes for a facility."""
    return await postgres.get_map_changes(facility_id, limit=limit)


@router.get("/facility/{facility_id}/traffic")
async def get_traffic_density(facility_id: str, range: str = Query("24h")):
    """Traffic density grid (robot position frequency heatmap)."""
    hours = RANGE_MAP.get(range, 24)
    return await calculate_traffic_density(facility_id, hours=hours)


@router.get("/facility/{facility_id}/heatmap")
async def get_incident_heatmap(facility_id: str, range: str = Query("30d")):
    """Incident heatmap (Gaussian-blurred incident positions)."""
    days = RANGE_MAP.get(range, 720) // 24 or 30
    return await calculate_incident_heatmap(facility_id, days=days)


@router.post("/facility/{facility_id}/upload")
async def upload_map(facility_id: str, file: UploadFile = File(...)):
    """Upload a floor plan image (PNG, JPG, SVG)."""
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": "file too large (max 10MB)"})
    map_id = await upload_floor_plan(facility_id, data, file.filename or "map.png")
    return {"status": "ok", "map_id": map_id}


class SlamIngestRequest(BaseModel):
    facility_id: str
    image_b64: str
    resolution: float = 0.05
    origin_x: float = 0.0
    origin_y: float = 0.0


@router.post("/ingest/slam")
async def ingest_slam(body: SlamIngestRequest):
    """Ingest a SLAM map as base64-encoded PNG."""
    map_id = await ingest_slam_map(
        facility_id=body.facility_id, image_b64=body.image_b64,
        resolution=body.resolution, origin_x=body.origin_x, origin_y=body.origin_y,
    )
    return {"status": "ok", "map_id": map_id}


class OccupancyGridRequest(BaseModel):
    facility_id: str
    data: list[int]
    width: int
    height: int
    resolution: float = 0.05
    origin_x: float = 0.0
    origin_y: float = 0.0


@router.post("/ingest/occupancy-grid")
async def ingest_occupancy(body: OccupancyGridRequest):
    """Ingest a ROS 2 OccupancyGrid."""
    map_id = await ingest_ros2_occupancy_grid(
        facility_id=body.facility_id, data=body.data,
        width=body.width, height=body.height,
        resolution=body.resolution, origin_x=body.origin_x, origin_y=body.origin_y,
    )
    return {"status": "ok", "map_id": map_id}
