from fastapi import APIRouter, Query

from app.analysis.productivity import calculate_productivity, calculate_incident_time_losses

router = APIRouter(prefix="/productivity", tags=["productivity"])

RANGE_MAP = {"1d": 24, "7d": 168, "30d": 720, "24h": 24}


@router.get("")
async def get_productivity(
    facility_id: str = Query(...),
    range: str = Query("30d", description="Time range: 1d, 7d, 30d"),
):
    """Full productivity report with breakdowns by root cause, robot, and hotspot."""
    hours = RANGE_MAP.get(range, 720)
    return await calculate_productivity(facility_id, hours=hours)


@router.post("/refresh")
async def refresh_time_losses(
    facility_id: str = Query(...),
    hours: int = Query(24),
):
    """Batch-calculate time_lost_seconds for recent incidents."""
    await calculate_incident_time_losses(facility_id, hours=hours)
    return {"status": "ok", "facility_id": facility_id}
