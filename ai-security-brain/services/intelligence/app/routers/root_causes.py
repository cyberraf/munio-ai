from collections import Counter, defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from app.db import postgres
from app.analysis.root_cause import attribute_root_causes

router = APIRouter(prefix="/root-causes", tags=["root-causes"])

RANGE_MAP = {"1d": 24, "7d": 168, "30d": 720, "24h": 24}


@router.get("")
async def get_root_cause_breakdown(
    facility_id: str = Query(...),
    range: str = Query("30d"),
):
    """Root cause breakdown with counts and percentages."""
    hours = RANGE_MAP.get(range, 720)
    since = datetime.utcnow() - timedelta(hours=hours)
    incidents = await postgres.get_incidents(facility_id=facility_id, since=since)

    real = [i for i in incidents if not i.get("is_hallucination")]
    total_real = len(real)

    cause_counts: dict[str, int] = Counter()
    cause_time: dict[str, float] = defaultdict(float)
    for i in real:
        c = i.get("root_cause") or "unattributed"
        cause_counts[c] += 1
        cause_time[c] += (i.get("time_lost_seconds") or 30) / 3600

    breakdown = [
        {
            "root_cause": cause,
            "count": count,
            "pct": round(count / max(total_real, 1) * 100, 1),
            "time_lost_hours": round(cause_time[cause], 2),
        }
        for cause, count in cause_counts.most_common()
    ]

    return {
        "facility_id": facility_id,
        "period_hours": hours,
        "total_real_incidents": total_real,
        "hallucinations_excluded": len(incidents) - total_real,
        "breakdown": breakdown,
    }


@router.post("/refresh")
async def refresh_root_causes(
    facility_id: str = Query(...),
    hours: int = Query(24),
):
    """Trigger root cause attribution for recent incidents."""
    await attribute_root_causes(facility_id, hours=hours)
    return {"status": "ok", "facility_id": facility_id}
