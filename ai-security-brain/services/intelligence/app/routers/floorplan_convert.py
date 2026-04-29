"""Floor plan image → clean/bounded/collision PNG conversion router.

Uses the floor-plan-fill pipeline (exact replica): binarize → remove text →
extract walls → seal outer shell → detect & remove doors → box fixtures →
output three PNGs (clean, bounded, collision).
"""

import logging
from pathlib import Path

import cv2
from fastapi import APIRouter, Query, HTTPException

from app.analysis.floorplan_cv import process, safe_imwrite
from app.config import settings
from app.db import postgres

logger = logging.getLogger("intelligence.routers.floorplan_convert")

router = APIRouter(prefix="/floorplan", tags=["floorplan"])


@router.post("/convert")
async def convert_floorplan(
    facility_id: str = Query(..., description="Facility ID"),
    floor_id: str = Query(None, description="Optional floor UUID — when provided, reads/writes from the floors table"),
):
    """Run the floor-plan-fill pipeline on a floor plan.

    Outputs three PNGs saved next to the original image:
      - clean.png:     white background, black walls
      - bounded.png:   original gray + red fixture bounding boxes
      - collision.png: white = free, black = wall/obstacle

    When floor_id is provided, reads the floor plan from the floors table
    and updates that row. Otherwise falls back to the facilities table.
    """

    pool = await postgres.get_pool()

    # Try floor-level first, then fall back to facility-level
    if floor_id:
        row = await pool.fetchrow(
            "SELECT facility_id, floor_plan_url, floor_plan_width, floor_plan_height FROM floors WHERE id = $1",
            floor_id,
        )
        if row:
            facility_id = row["facility_id"]
    else:
        row = await pool.fetchrow(
            "SELECT floor_plan_url, floor_plan_width, floor_plan_height FROM facilities WHERE id = $1",
            facility_id,
        )

    if not row or not row["floor_plan_url"]:
        raise HTTPException(status_code=404, detail="No floor plan uploaded")

    # Always look for the original PNG/JPG.
    # Floor-level uploads go to data/floor-plans/{facility_id}/{floor_id}/plan.png
    # Facility-level (legacy) uploads go to data/floor-plans/{facility_id}/plan.png
    search_bases: list[Path] = []

    # Floor-specific paths first (when floor_id is provided)
    if floor_id:
        if settings.CORE_DATA_DIR:
            search_bases.append(Path(settings.CORE_DATA_DIR) / "floor-plans" / facility_id / floor_id)
        router_file = Path(__file__).resolve()
        services_dir = router_file.parent.parent.parent.parent
        search_bases.extend([
            services_dir / "core" / "data" / "floor-plans" / facility_id / floor_id,
            Path.cwd().parent / "core" / "data" / "floor-plans" / facility_id / floor_id,
            Path.cwd() / "data" / "floor-plans" / facility_id / floor_id,
        ])

    # Facility-level fallback paths
    if settings.CORE_DATA_DIR:
        search_bases.append(Path(settings.CORE_DATA_DIR) / "floor-plans" / facility_id)
    router_file = Path(__file__).resolve()
    services_dir = router_file.parent.parent.parent.parent
    search_bases.extend([
        services_dir / "core" / "data" / "floor-plans" / facility_id,
        Path.cwd().parent / "core" / "data" / "floor-plans" / facility_id,
        Path.cwd() / "data" / "floor-plans" / facility_id,
    ])

    image_path: Path | None = None
    for base in search_bases:
        for name in ["plan.png", "plan.jpg", "plan.jpeg"]:
            p = base / name
            if p.exists():
                image_path = p
                break
        if image_path:
            break

    if not image_path:
        tried = [str(b) for b in search_bases]
        logger.error("Floor plan not found for %s. Searched: %s", facility_id, tried)
        raise HTTPException(
            status_code=404,
            detail="Floor plan image not found. Set CORE_DATA_DIR env var.",
        )

    # Read and decode image
    img = cv2.imread(str(image_path))
    if img is None:
        raise HTTPException(status_code=500, detail="Could not decode floor plan image")

    # Run the floor-plan-fill pipeline (exact replica)
    clean, bounded, collision = process(img)

    h, w = clean.shape[:2]
    logger.info(
        "Floor-plan-fill pipeline done for %s: %dx%d",
        facility_id, w, h,
    )

    # Save the three output PNGs
    output_dir = image_path.parent
    clean_path = output_dir / "clean.png"
    bounded_path = output_dir / "bounded.png"
    collision_path = output_dir / "collision.png"

    safe_imwrite(str(clean_path), clean)
    safe_imwrite(str(bounded_path), bounded)
    safe_imwrite(str(collision_path), collision)

    # Update the floor_plan_url to point at clean.png (path matches where we saved it)
    if floor_id:
        clean_url = f"/data/floor-plans/{facility_id}/{floor_id}/clean.png"
        await pool.execute(
            "UPDATE floors SET floor_plan_url = $2, floor_plan_width = $3, floor_plan_height = $4 WHERE id = $1",
            floor_id, clean_url, w, h,
        )
    else:
        clean_url = f"/data/floor-plans/{facility_id}/clean.png"
        await pool.execute(
            "UPDATE facilities SET floor_plan_url = $2, floor_plan_width = $3, floor_plan_height = $4 WHERE id = $1",
            facility_id, clean_url, w, h,
        )

    return {
        "status": "converted",
        "facility_id": facility_id,
        "clean_url": clean_url,
        "bounded_url": f"/data/floor-plans/{facility_id}/bounded.png",
        "collision_url": f"/data/floor-plans/{facility_id}/collision.png",
        "width": w,
        "height": h,
    }


@router.get("/outputs")
async def get_floorplan_outputs(
    facility_id: str = Query(..., description="Facility ID"),
):
    """Return URLs for the three conversion outputs (clean, bounded, collision)."""
    base = f"/data/floor-plans/{facility_id}"
    return {
        "facility_id": facility_id,
        "clean_url": f"{base}/clean.png",
        "bounded_url": f"{base}/bounded.png",
        "collision_url": f"{base}/collision.png",
    }
