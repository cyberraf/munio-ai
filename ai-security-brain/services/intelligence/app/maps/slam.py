"""SLAM map ingestion — accepts PNG images or ROS 2 OccupancyGrid data."""

import base64
import logging
import os
import uuid
from datetime import datetime

import numpy as np
from PIL import Image

from app.db import postgres

logger = logging.getLogger("intelligence.maps.slam")

MAPS_DIR = os.environ.get("MAPS_DIR", "/tmp/asb_maps")
os.makedirs(MAPS_DIR, exist_ok=True)


async def ingest_slam_map(
    facility_id: str,
    image_b64: str,
    resolution: float = 0.05,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    source: str = "slam",
    captured_at: datetime | None = None,
) -> str:
    """Ingest a base64-encoded PNG map image. Returns the new map ID."""
    image_data = base64.b64decode(image_b64)
    map_id = str(uuid.uuid4())[:12]
    filename = f"{facility_id}_{map_id}.png"
    filepath = os.path.join(MAPS_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(image_data)

    img = Image.open(filepath)
    width, height = img.size
    img.close()

    prev_map = await postgres.get_active_map(facility_id)

    new_id = await postgres.insert_facility_map({
        "facility_id": facility_id,
        "map_type": "slam",
        "source": source,
        "resolution": resolution,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "width_pixels": width,
        "height_pixels": height,
        "s3_url": filepath,
        "is_active": True,
        "captured_at": captured_at or datetime.utcnow(),
    })

    await postgres.deactivate_facility_maps(facility_id, except_id=new_id)
    logger.info(f"[{facility_id}] ingested map: {width}x{height}px, res={resolution}m/px")

    if prev_map:
        try:
            from app.maps.change_detection import detect_map_changes
            await detect_map_changes(
                facility_id,
                old_path=prev_map["s3_url"],
                new_path=filepath,
                old_id=str(prev_map["id"]),
                new_id=new_id,
                resolution=resolution,
                origin_x=origin_x,
                origin_y=origin_y,
            )
        except Exception as e:
            logger.error(f"[{facility_id}] change detection failed: {e}")

    return new_id


async def ingest_ros2_occupancy_grid(
    facility_id: str,
    data: list[int],
    width: int,
    height: int,
    resolution: float = 0.05,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> str:
    """Convert a ROS 2 OccupancyGrid to PNG and ingest."""
    grid = np.array(data, dtype=np.int8).reshape((height, width))
    img_array = np.full((height, width), 128, dtype=np.uint8)
    img_array[grid == 0] = 255
    img_array[grid == 100] = 0
    img_array[grid == -1] = 128
    img_array = np.flipud(img_array)

    img = Image.fromarray(img_array, mode="L")
    temp_path = os.path.join(MAPS_DIR, f"_temp_grid_{facility_id}.png")
    img.save(temp_path)

    with open(temp_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    os.unlink(temp_path)

    return await ingest_slam_map(
        facility_id=facility_id, image_b64=b64, resolution=resolution,
        origin_x=origin_x, origin_y=origin_y, source="ros2_occupancy_grid",
    )


async def upload_floor_plan(facility_id: str, image_data: bytes, filename: str) -> str:
    """Upload a user-provided floor plan image."""
    map_id = str(uuid.uuid4())[:12]
    ext = os.path.splitext(filename)[1] or ".png"
    saved_name = f"{facility_id}_floorplan_{map_id}{ext}"
    filepath = os.path.join(MAPS_DIR, saved_name)

    with open(filepath, "wb") as f:
        f.write(image_data)

    try:
        img = Image.open(filepath)
        width, height = img.size
        img.close()
    except Exception:
        width, height = 0, 0

    new_id = await postgres.insert_facility_map({
        "facility_id": facility_id, "map_type": "floor_plan", "source": "upload",
        "resolution": None, "origin_x": 0, "origin_y": 0,
        "width_pixels": width, "height_pixels": height, "s3_url": filepath,
        "is_active": True, "captured_at": datetime.utcnow(),
    })
    await postgres.deactivate_facility_maps(facility_id, except_id=new_id)
    logger.info(f"[{facility_id}] uploaded floor plan: {saved_name} ({width}x{height})")
    return new_id
