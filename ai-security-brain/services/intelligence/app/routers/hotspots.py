from fastapi import APIRouter, Query

from app.db import postgres
from app.analysis.spatial import detect_hotspots

router = APIRouter(prefix="/hotspots", tags=["hotspots"])


@router.get("")
async def list_hotspots(facility_id: str = Query(..., description="Facility ID")):
    """Get detected spatial hotspots for a facility."""
    return await postgres.get_hotspots(facility_id)


@router.post("/refresh")
async def refresh_hotspots(
    facility_id: str = Query(...),
    days: int = Query(30, description="Analysis window in days"),
):
    """Trigger hotspot re-detection for a facility."""
    hotspots = await detect_hotspots(facility_id, days=days)
    return {"status": "ok", "facility_id": facility_id, "hotspots_detected": len(hotspots) if hotspots else 0}
