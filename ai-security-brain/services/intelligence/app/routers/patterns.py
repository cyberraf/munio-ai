from fastapi import APIRouter, Query

from app.db import postgres
from app.analysis.temporal import detect_patterns

router = APIRouter(prefix="/patterns", tags=["patterns"])


@router.get("")
async def list_patterns(facility_id: str = Query(..., description="Facility ID")):
    """Get detected temporal patterns for a facility."""
    return await postgres.get_patterns(facility_id)


@router.post("/refresh")
async def refresh_patterns(
    facility_id: str = Query(...),
    days: int = Query(30, description="Analysis window in days"),
):
    """Trigger temporal pattern re-detection for a facility."""
    patterns = await detect_patterns(facility_id, days=days)
    return {"status": "ok", "facility_id": facility_id, "patterns_detected": len(patterns) if patterns else 0}
