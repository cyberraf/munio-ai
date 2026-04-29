from pydantic import BaseModel
from fastapi import APIRouter, Query

from app.db import postgres
from app.analysis.recommendations import generate_recommendations

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("")
async def list_recommendations(
    facility_id: str = Query(...),
    status: str = Query("open", description="Filter by status: open, implemented, dismissed"),
):
    """Get ranked recommendations for a facility."""
    return await postgres.get_recommendations(facility_id, status=status)


class StatusUpdate(BaseModel):
    status: str  # "implemented" or "dismissed"


@router.patch("/{rec_id}")
async def update_recommendation(rec_id: str, body: StatusUpdate):
    """Update recommendation status. Set to 'implemented' or 'dismissed'."""
    if body.status not in ("implemented", "dismissed", "open"):
        return {"error": "status must be 'implemented', 'dismissed', or 'open'"}
    await postgres.update_recommendation_status(rec_id, body.status)
    return {"status": "ok", "id": rec_id, "new_status": body.status}


@router.post("/refresh")
async def refresh_recommendations(facility_id: str = Query(...)):
    """Trigger recommendation generation for a facility."""
    await generate_recommendations(facility_id)
    return {"status": "ok", "facility_id": facility_id}
