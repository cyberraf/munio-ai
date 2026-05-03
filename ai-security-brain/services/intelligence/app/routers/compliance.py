"""Compliance endpoints: report generation, framework list, and monitoring uptime."""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db import clickhouse, postgres
from app.reports.compliance_report import generate_compliance_report

logger = logging.getLogger("intelligence.routers.compliance")

router = APIRouter(prefix="/compliance", tags=["compliance"])

SUPPORTED_FRAMEWORKS = ["EU_AI_ACT", "ISO_10218", "OSHA_1910", "ANSI_R15_08"]

_GAP_THRESHOLD = timedelta(minutes=5)


class ComplianceReportRequest(BaseModel):
    facility_id: str
    framework: str
    start_date: date
    end_date: date


@router.post("/report")
async def generate_report(body: ComplianceReportRequest):
    """Generate a compliance report PDF and return the URL, score, and summary."""
    if body.framework not in SUPPORTED_FRAMEWORKS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported framework '{body.framework}'. Valid: {SUPPORTED_FRAMEWORKS}",
        )
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    try:
        path = await generate_compliance_report(
            body.facility_id, body.framework, body.start_date, body.end_date
        )
    except Exception as e:
        logger.exception("Compliance report generation failed")
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    # Read back the row upserted during generation for compliance_score + summary.
    pool = await postgres.get_pool()
    period_start = datetime(
        body.start_date.year, body.start_date.month, body.start_date.day,
        tzinfo=timezone.utc,
    )
    row = await pool.fetchrow(
        """SELECT report_url, summary_findings, recommendations
        FROM compliance_reports
        WHERE facility_id = $1 AND report_type = $2 AND period_start = $3
        ORDER BY generated_at DESC LIMIT 1""",
        body.facility_id, body.framework, period_start,
    )

    compliance_score = 0.0
    summary: dict[str, Any] = {}
    if row:
        try:
            findings = json.loads(row["summary_findings"] or "{}")
            compliance_score = float(findings.get("compliance_score", 0.0))
            summary = findings
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    return {
        "report_url": path or (row["report_url"] if row else ""),
        "compliance_score": compliance_score,
        "summary": summary,
    }


@router.get("/frameworks")
async def list_frameworks():
    """Return the supported regulatory frameworks."""
    return SUPPORTED_FRAMEWORKS


@router.get("/uptime")
async def get_monitoring_uptime(
    facility_id: str = Query(...),
    days: int = Query(default=30, ge=1, le=365),
):
    """Return monitoring uptime derived from ClickHouse telemetry, detecting 5+ minute gaps."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    total_hours = days * 24.0

    # One aggregated row per minute that has any telemetry — avoids scanning raw rows.
    client = clickhouse.get_client()
    q = """SELECT toStartOfMinute(timestamp) AS minute
    FROM telemetry
    WHERE facility_id = %(facility_id)s
      AND timestamp >= %(start)s AND timestamp <= %(end)s
    GROUP BY minute ORDER BY minute"""
    try:
        result = client.query(q, parameters={"facility_id": facility_id, "start": start, "end": end})
    except Exception as e:
        logger.warning("ClickHouse uptime query failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to query telemetry data")

    # Normalize ClickHouse naive datetimes to UTC.
    minutes: list[datetime] = []
    for (dt,) in result.result_rows:
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        minutes.append(dt)

    if not minutes:
        return {
            "facility_id": facility_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total_hours": round(total_hours, 2),
            "monitored_hours": 0.0,
            "uptime_pct": 0.0,
            "gaps": [],
        }

    gaps = []
    gap_seconds = 0.0

    def _record_gap(gap_start: datetime, gap_end: datetime) -> None:
        delta = gap_end - gap_start
        if delta > _GAP_THRESHOLD:
            gaps.append({
                "start": gap_start.isoformat(),
                "end": gap_end.isoformat(),
                "duration_minutes": round(delta.total_seconds() / 60, 1),
            })
            nonlocal gap_seconds
            gap_seconds += delta.total_seconds()

    # Silence before first event and after last event counts as a gap.
    _record_gap(start, minutes[0])
    for prev, curr in zip(minutes, minutes[1:]):
        _record_gap(prev, curr)
    _record_gap(minutes[-1], end)

    total_seconds = total_hours * 3600
    monitored_seconds = max(0.0, total_seconds - gap_seconds)

    return {
        "facility_id": facility_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_hours": round(total_hours, 2),
        "monitored_hours": round(monitored_seconds / 3600, 2),
        "uptime_pct": round(monitored_seconds / total_seconds * 100, 2),
        "gaps": gaps,
    }
