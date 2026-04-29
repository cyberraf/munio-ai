from fastapi import APIRouter, Query

from app.db import postgres
from app.analysis.robot_health import score_robot_health

router = APIRouter(prefix="/health", tags=["robot-health"])


@router.get("")
async def list_robot_health(facility_id: str = Query(...)):
    """Latest health scores for all robots in a facility, sorted worst-first."""
    scores = await postgres.get_latest_robot_health(facility_id)
    scores.sort(key=lambda s: s.get("overall_score", 100))
    return scores


@router.get("/robot/{robot_id}")
async def get_robot_health_history(robot_id: str, days: int = Query(30)):
    """Health score history for a single robot."""
    return await postgres.get_robot_health_history(robot_id, days=days)


@router.post("/refresh")
async def refresh_health(facility_id: str = Query(...)):
    """Trigger health scoring for all robots in a facility."""
    await score_robot_health(facility_id)
    return {"status": "ok", "facility_id": facility_id}
