"""Map change detection using OpenCV pixel-wise diff and contour analysis.

Compares two SLAM maps, finds significant structural changes, classifies them,
and inserts map_changes records.
"""

import logging
import os

import cv2

from app.db import postgres

logger = logging.getLogger("intelligence.maps.change_detection")

DIFF_THRESHOLD = 50     # pixel value difference to count as changed
MIN_CONTOUR_AREA = 100  # pixels — filter noise


async def detect_map_changes(
    facility_id: str,
    old_path: str,
    new_path: str,
    old_id: str,
    new_id: str,
    resolution: float = 0.05,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
):
    """Compare two map images and detect structural changes.

    1. Load both images as grayscale.
    2. Resize to match if dimensions differ.
    3. Compute pixel-wise absolute difference.
    4. Threshold to find significant changes.
    5. Find contours of changed regions.
    6. Filter tiny changes (noise).
    7. Classify and insert map_changes records.
    """
    if not os.path.exists(old_path) or not os.path.exists(new_path):
        logger.warning(f"[{facility_id}] map files missing: {old_path} or {new_path}")
        return

    old_img = cv2.imread(old_path, cv2.IMREAD_GRAYSCALE)
    new_img = cv2.imread(new_path, cv2.IMREAD_GRAYSCALE)

    if old_img is None or new_img is None:
        logger.error(f"[{facility_id}] could not read map images")
        return

    # Resize to match if needed
    if old_img.shape != new_img.shape:
        h = min(old_img.shape[0], new_img.shape[0])
        w = min(old_img.shape[1], new_img.shape[1])
        old_img = cv2.resize(old_img, (w, h))
        new_img = cv2.resize(new_img, (w, h))

    # Pixel-wise diff
    diff = cv2.absdiff(old_img, new_img)

    # Threshold
    _, thresh = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)

    # Morphological close to merge nearby changed pixels
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    # Find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    changes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < MIN_CONTOUR_AREA:
            continue

        # Bounding box center in pixel coordinates
        M = cv2.moments(contour)
        if M["m00"] == 0:
            continue
        cx_px = int(M["m10"] / M["m00"])
        cy_px = int(M["m01"] / M["m00"])

        # Convert pixel → world coordinates
        world_x = origin_x + cx_px * resolution
        world_y = origin_y + (old_img.shape[0] - cy_px) * resolution  # flip Y

        # Classify change type
        old_val = int(old_img[cy_px, cx_px])
        new_val = int(new_img[cy_px, cx_px])

        if old_val > 200 and new_val < 50:
            change_type = "new_obstacle"
            desc = "Free space is now occupied — new obstacle or structure detected"
        elif old_val < 50 and new_val > 200:
            change_type = "removed_obstacle"
            desc = "Previously occupied space is now free — obstacle removed"
        else:
            change_type = "moved_object"
            desc = "Structural change detected — object may have been moved or repositioned"

        area_m2 = area * resolution * resolution
        impact = "low" if area_m2 < 1 else ("medium" if area_m2 < 5 else "high")

        changes.append({
            "facility_id": facility_id,
            "old_map_id": old_id,
            "new_map_id": new_id,
            "change_type": change_type,
            "location_x": round(world_x, 2),
            "location_y": round(world_y, 2),
            "description": f"{desc} (area: {area_m2:.1f} m²)",
            "impact": impact,
        })

    # Insert changes
    for c in changes:
        await postgres.insert_map_change(c)

    logger.info(
        f"[{facility_id}] detected {len(changes)} map changes "
        f"({len(contours)} contours, {len(contours) - len(changes)} filtered as noise)"
    )
