import asyncio
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.db import postgres
from app.reports.generator import generate_monthly_pdf

router = APIRouter(prefix="/reports", tags=["reports"])

# Track in-progress report generation
_generating: dict[str, str] = {}  # report_id → status


class GenerateRequest(BaseModel):
    facility_id: str
    month: str  # "2026-04"


@router.post("/generate")
async def generate_report(body: GenerateRequest, background_tasks: BackgroundTasks):
    """Trigger async report generation. Returns report_id to poll status."""
    report_id = str(uuid.uuid4())[:8]
    _generating[report_id] = "generating"

    async def _run():
        try:
            path = await generate_monthly_pdf(body.facility_id, body.month)
            _generating[report_id] = f"done:{path}" if path else "error:generation failed"
        except Exception as e:
            _generating[report_id] = f"error:{e}"

    background_tasks.add_task(asyncio.coroutine(_run) if False else _run)  # noqa
    return {"status": "generating", "report_id": report_id}


@router.get("/status/{report_id}")
async def report_status(report_id: str):
    """Check generation status."""
    status = _generating.get(report_id)
    if status is None:
        return JSONResponse(status_code=404, content={"error": "report_id not found"})
    if status.startswith("done:"):
        return {"status": "done", "report_id": report_id, "path": status[5:]}
    if status.startswith("error:"):
        return {"status": "error", "report_id": report_id, "error": status[6:]}
    return {"status": "generating", "report_id": report_id}


@router.get("")
async def list_reports(facility_id: str = Query(...)):
    """List all generated reports for a facility."""
    return await postgres.get_monthly_reports(facility_id)


@router.get("/{report_id}/download")
async def download_report(report_id: str):
    """Download a report PDF by looking up the report_url in the DB."""
    # Check in-progress reports first
    status = _generating.get(report_id)
    if status and status.startswith("done:"):
        path = status[5:]
        if os.path.exists(path):
            return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))

    # Try to find by UUID in the monthly_reports table
    # For now, if a direct file path was passed as report_id, serve it
    return JSONResponse(status_code=404, content={"error": "report not found or not ready"})
