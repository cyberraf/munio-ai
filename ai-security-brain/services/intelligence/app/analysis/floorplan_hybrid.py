"""Hybrid floor plan conversion pipeline.

Combines three complementary techniques:

1. **OpenCV** — extracts precise pixel-accurate geometry (walls via Hough lines,
   rooms via contours, fixtures via shape analysis, door arcs via ellipse fits).
2. **Tesseract OCR** — reads room labels and dimension strings exactly as printed.
3. **Claude Vision** — used ONLY for semantic identification (this region is a
   bedroom, this fixture is a toilet). Coordinates are never asked of Claude.

The result is a structured SVG where every wall, room, fixture and door sits
at its measured pixel location, while the human-readable labels come from real
OCR rather than Claude's best guess.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any

import anthropic
import cv2
import numpy as np

from app.analysis.floorplan_vision import SVG_STYLES, _parse_json
from app.config import settings
from app.maps.svg_symbols import render_element_to_svg, svg_door_swing

logger = logging.getLogger("intelligence.analysis.floorplan_hybrid")

# Tesseract is required for the hybrid pipeline to produce usable output:
# without OCR labels every room is unnamed and we end up shipping a noisy
# CV-only trace (which looks worse than the pure Claude vision result).
# We import the wrapper opportunistically AND verify the binary is actually
# installed; if either is missing, `convert_floor_plan_hybrid` will return
# None so the router falls through to the Claude vision pipeline.
try:
    import pytesseract  # type: ignore

    _HAS_PYTESSERACT = True
except ImportError:
    _HAS_PYTESSERACT = False
    logger.warning("pytesseract Python package not installed")


def _tesseract_binary_available() -> bool:
    """True iff the Tesseract binary is on PATH or pointed to by TESSERACT_CMD."""
    if not _HAS_PYTESSERACT:
        return False
    # Allow an explicit override for Windows installs that didn't add
    # `C:\Program Files\Tesseract-OCR` to PATH.
    explicit = os.environ.get("TESSERACT_CMD")
    if explicit and Path(explicit).exists():
        pytesseract.pytesseract.tesseract_cmd = explicit
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


_HAS_TESSERACT = _tesseract_binary_available()
if not _HAS_TESSERACT:
    logger.warning("Tesseract binary not available — hybrid pipeline will be skipped")


# ─── Step 1: Image preprocessing ────────────────────────────────────────────


def preprocess_floor_plan(image_bytes: bytes) -> dict:
    """Decode → resize → grayscale → binary → edges. Returns intermediate views.

    The binary image returned by this step has dimension labels and other
    text glyphs *removed* via connected-component analysis. Removing text
    before geometry extraction prevents text strokes from polluting the
    Hough wall detector and the fixture/door contour detectors.
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode floor plan image")

    height, width = img.shape[:2]

    # Resize to <= 2000px on the longest side, preserving aspect ratio.
    max_dim = 2000
    if max(height, width) > max_dim:
        scale = max_dim / max(height, width)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        height, width = img.shape[:2]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Adaptive threshold handles uneven lighting in scanned plans.
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 5
    )
    _, binary_simple = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel_small = np.ones((2, 2), np.uint8)
    kernel_medium = np.ones((3, 3), np.uint8)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel_medium, iterations=1)
    cleaned = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel_small, iterations=1)

    # Strip text/dimension glyphs before any geometry extraction. We keep
    # several intermediate binaries so each downstream stage can pick the
    # most appropriate one — Tesseract still runs on the original grayscale.
    cleaned_no_text = _remove_text_and_numbers(cleaned)

    # Seal the outer shell so room contour detection isn't fooled by windows
    # or scan gaps in the exterior wall.
    sealed = _seal_outer_walls(cleaned_no_text)

    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    return {
        "original": img,
        "gray": gray,
        # `binary` is the post-text, post-seal version — use this for room
        # contour extraction and any operation that wants closed shells.
        "binary": sealed,
        # `binary_no_text` keeps the text-stripped binary BEFORE the outer
        # contour was redrawn. Door detection wants this so the sealed
        # contour stroke doesn't pollute the residual ink.
        "binary_no_text": cleaned_no_text,
        "binary_raw": cleaned,
        "binary_simple": binary_simple,
        "edges": edges,
        "width": width,
        "height": height,
    }


# Connected-component thresholds for the text/number stripper. Ported from
# `floor-plan-fill-main/fix_floorplan_cleanup_and_bound.py`. The fill-ratio
# bound is what makes this safe — solid fixtures pack >55% of their bbox,
# whereas glyph strokes are sparse outlines.
_TEXT_MAX_HEIGHT = 28
_TEXT_MAX_WIDTH = 80
_TEXT_MAX_AREA = 900
_TEXT_MAX_FILL = 0.55


def _is_textlike_component(w: int, h: int, area: int) -> bool:
    if area <= 0:
        return False
    fill = area / (w * h + 1e-6)
    return (
        h <= _TEXT_MAX_HEIGHT
        and w <= _TEXT_MAX_WIDTH
        and area <= _TEXT_MAX_AREA
        and fill <= _TEXT_MAX_FILL
    )


def _remove_text_and_numbers(binary: np.ndarray) -> np.ndarray:
    """Drop dimension labels and small text glyphs from a binary image.

    Walls survive because their bounding boxes far exceed `_TEXT_MAX_*`,
    fixtures survive because their fill ratio is much higher than text
    strokes. Returns a fresh binary image (does not mutate the input).
    """
    num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out = binary.copy()
    for i in range(1, num):
        _x, _y, w, h, area = stats[i]
        if _is_textlike_component(int(w), int(h), int(area)):
            out[labels == i] = 0
    return out


# Outer-shell sealing constants — large kernel because window openings can
# be many pixels wide on a scanned plan.
_OUTER_CLOSE_KERNEL = 121
_OUTER_CONTOUR_THICKNESS = 6


def _seal_outer_walls(walls: np.ndarray) -> np.ndarray:
    """Force the building's outer shell closed.

    Walls extracted by Hough may have gaps where windows or doors interrupt
    the exterior. We aggressively MORPH_CLOSE the wall mask, then redraw the
    largest external contour back onto the wall image with a thick stroke.
    Anything inside the building stays unchanged.
    """
    if walls.size == 0 or not np.any(walls):
        return walls
    big_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (_OUTER_CLOSE_KERNEL, _OUTER_CLOSE_KERNEL)
    )
    closed = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, big_kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return walls
    shell = np.zeros_like(walls)
    largest = max(contours, key=cv2.contourArea)
    cv2.drawContours(shell, [largest], -1, 255, thickness=_OUTER_CONTOUR_THICKNESS)
    return cv2.bitwise_or(walls, shell)


# ─── Step 2: Geometry extraction (OpenCV) ───────────────────────────────────


def extract_walls(preprocessed: dict) -> list[dict]:
    """Detect wall segments via Probabilistic Hough Line Transform.

    The thresholds here are deliberately stricter than the OpenCV defaults:
    floor plans contain a lot of short stubs (text strokes, fixture edges,
    door arcs) that the lower defaults would mistake for walls. We require
    a minimum length of 8% of the image diagonal so only real walls survive.
    """
    edges = preprocessed["edges"]
    binary = preprocessed["binary"]
    img_h, img_w = edges.shape[:2]
    diag = float(np.sqrt(img_w * img_w + img_h * img_h))
    min_len = max(int(diag * 0.08), 40)

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=80,
        minLineLength=min_len,
        maxLineGap=int(diag * 0.01),
    )

    if lines is None:
        return []

    walls: list[dict] = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        length = float(np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2))
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180)

        if length < min_len:
            continue

        is_horizontal = angle < 10 or angle > 170
        is_vertical = abs(angle - 90) < 10

        # Drop non-orthogonal segments — they're almost always noise.
        if not (is_horizontal or is_vertical):
            continue

        if is_horizontal:
            y_avg = (y1 + y2) // 2
            y1 = y2 = y_avg
        else:
            x_avg = (x1 + x2) // 2
            x1 = x2 = x_avg

        thickness = _estimate_wall_thickness(binary, x1, y1, x2, y2)
        wall_type = "exterior" if thickness > 8 else "interior"

        walls.append(
            {
                "start_x": int(x1),
                "start_y": int(y1),
                "end_x": int(x2),
                "end_y": int(y2),
                "thickness": thickness,
                "wall_type": wall_type,
                "length_px": int(length),
                "angle": round(angle, 1),
            }
        )

    return _merge_collinear_walls(walls)


def _estimate_wall_thickness(binary, x1, y1, x2, y2, sample_count: int = 5) -> int:
    """Sample perpendicular cross-sections to measure wall thickness."""
    dx = x2 - x1
    dy = y2 - y1
    length = max(np.sqrt(dx * dx + dy * dy), 1)

    nx = -dy / length
    ny = dx / length

    thicknesses: list[int] = []
    for i in range(sample_count):
        t = (i + 1) / (sample_count + 1)
        cx = int(x1 + t * dx)
        cy = int(y1 + t * dy)

        thickness = 0
        for d in range(-20, 21):
            px = int(cx + d * nx)
            py = int(cy + d * ny)
            if 0 <= px < binary.shape[1] and 0 <= py < binary.shape[0]:
                if binary[py, px] > 128:
                    thickness += 1

        if thickness > 0:
            thicknesses.append(thickness)

    return int(np.median(thicknesses)) if thicknesses else 6


def _are_collinear(w1: dict, w2: dict, distance_threshold: float = 5.0) -> bool:
    """True iff w2 lies on the same infinite line as w1 (within tolerance)."""
    x1, y1 = w1["start_x"], w1["start_y"]
    x2, y2 = w1["end_x"], w1["end_y"]
    dx = x2 - x1
    dy = y2 - y1
    length = max(np.sqrt(dx * dx + dy * dy), 1)

    for px, py in (
        (w2["start_x"], w2["start_y"]),
        (w2["end_x"], w2["end_y"]),
    ):
        # Perpendicular distance from (px,py) to the infinite line through w1.
        cross = abs((px - x1) * dy - (py - y1) * dx)
        dist = cross / length
        if dist > distance_threshold:
            return False
    return True


def _merge_collinear_walls(
    walls: list[dict],
    distance_threshold: float = 5.0,
    angle_threshold: float = 5.0,
) -> list[dict]:
    """Glue wall segments that lie on the same line but were broken by gaps."""
    merged: list[dict] = []
    used: set[int] = set()

    for i, w1 in enumerate(walls):
        if i in used:
            continue

        group = [w1]
        used.add(i)

        for j, w2 in enumerate(walls):
            if j in used:
                continue
            if abs(w1["angle"] - w2["angle"]) > angle_threshold:
                continue
            if _are_collinear(w1, w2, distance_threshold):
                group.append(w2)
                used.add(j)

        all_x = [w["start_x"] for w in group] + [w["end_x"] for w in group]
        all_y = [w["start_y"] for w in group] + [w["end_y"] for w in group]

        if group[0]["angle"] < 10 or group[0]["angle"] > 170:
            avg_y = int(np.mean(all_y))
            merged.append(
                {
                    **group[0],
                    "start_x": min(all_x),
                    "start_y": avg_y,
                    "end_x": max(all_x),
                    "end_y": avg_y,
                }
            )
        else:
            avg_x = int(np.mean(all_x))
            merged.append(
                {
                    **group[0],
                    "start_x": avg_x,
                    "start_y": min(all_y),
                    "end_x": avg_x,
                    "end_y": max(all_y),
                }
            )

    return merged


def extract_rooms(preprocessed: dict, _walls: list[dict] | None = None) -> list[dict]:
    """Detect enclosed rooms via contour analysis on a dilated binary image."""
    binary = preprocessed["binary"].copy()

    kernel = np.ones((5, 5), np.uint8)
    dilated = cv2.dilate(binary, kernel, iterations=2)
    inverted = cv2.bitwise_not(dilated)

    contours, _hierarchy = cv2.findContours(
        inverted, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
    )

    width, height = preprocessed["width"], preprocessed["height"]
    min_room_area = width * height * 0.01
    max_room_area = width * height * 0.8

    rooms: list[dict] = []
    for i, contour in enumerate(contours):
        area = cv2.contourArea(contour)
        if area < min_room_area or area > max_room_area:
            continue

        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        x, y, w, h = cv2.boundingRect(contour)

        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        cx = int(moments["m10"] / moments["m00"])
        cy = int(moments["m01"] / moments["m00"])

        polygon = [[int(p[0][0]), int(p[0][1])] for p in approx]

        rooms.append(
            {
                "id": f"room_{i}",
                "polygon": polygon,
                "bbox": {"x": x, "y": y, "w": w, "h": h},
                "center": {"x": cx, "y": cy},
                "area_px": int(area),
                "label": "",
                "room_type": "",
                "dimensions": "",
            }
        )

    return rooms


# ─── Door detection (arc-band scorer, ported from floor-plan-fill-main) ─────
#
# The previous implementation ran cv2.fitEllipse over every contour and
# accepted anything roughly circular with an arc-fraction in (0.2, 0.32).
# That approach over-detected: it fired on text loops, fixture rims, and
# random scan artefacts (the swirling dashes you saw in the broken render).
#
# The new approach is borrowed from `floor-plan-fill-main`: for each
# residual component, sample points along candidate quarter-circle arcs at
# multiple centers/radii/orientations and score what fraction of them land
# on ink. Combined with a leaf-line check and explicit stairs/fixture
# rejectors, it cuts false positives dramatically.

_DOOR_MIN_SIZE = 10
_DOOR_MAX_SIZE = 140
_DOOR_MAX_FILL = 0.35
_DOOR_ARC_SCORE = 0.42
_DOOR_ARC_ONLY_SCORE = 0.50
_DOOR_LEAF_MIN = 6
_DOOR_LOCAL_CLOSE = 3


def _arc_band_score(
    mask: np.ndarray,
    cx: float,
    cy: float,
    r: float,
    start_deg: float,
    sweep: float = 90.0,
    band: int = 2,
    samples: int = 72,
) -> float:
    """Fraction of points along a quarter-arc that land on ink."""
    h, w = mask.shape
    angles = np.deg2rad(np.linspace(start_deg, start_deg + sweep, samples))
    hits = 0
    valid = 0
    for a in angles:
        x = int(round(cx + r * np.cos(a)))
        y = int(round(cy + r * np.sin(a)))
        if x < 0 or x >= w or y < 0 or y >= h:
            continue
        valid += 1
        x0 = max(0, x - band)
        x1 = min(w, x + band + 1)
        y0 = max(0, y - band)
        y1 = min(h, y + band + 1)
        if np.any(mask[y0:y1, x0:x1] > 0):
            hits += 1
    return hits / valid if valid else 0.0


def _best_arc_score(mask: np.ndarray) -> tuple[float, int, int, float, float]:
    """Try every plausible center/radius/orientation; return the best.

    Returns (score, hinge_x, hinge_y, radius, start_deg).
    """
    h, w = mask.shape
    if h < 3 or w < 3:
        return 0.0, 0, 0, 0.0, 0.0

    pts = np.column_stack(np.where(mask > 0))
    if len(pts) < 8:
        return 0.0, 0, 0, 0.0, 0.0

    centers = [
        (0, 0),
        (w - 1, 0),
        (0, h - 1),
        (w - 1, h - 1),
        (w // 2, 0),
        (w // 2, h - 1),
        (0, h // 2),
        (w - 1, h // 2),
        (w // 2, h // 2),
    ]
    max_dim = max(h, w)
    radii = np.linspace(max(5, 0.20 * max_dim), 0.95 * max_dim, 8)

    best = 0.0
    best_cx, best_cy = 0, 0
    best_r = 0.0
    best_start = 0.0
    for cx, cy in centers:
        for r in radii:
            for start in (0, 90, 180, 270):
                s = _arc_band_score(mask, cx, cy, r, start)
                if s > best:
                    best = s
                    best_cx, best_cy = cx, cy
                    best_r = r
                    best_start = start
    return best, best_cx, best_cy, best_r, best_start


def _has_leaf_line(mask: np.ndarray) -> bool:
    """A real door has a short straight 'leaf' segment alongside the arc."""
    lines = cv2.HoughLinesP(
        mask,
        1,
        np.pi / 180,
        threshold=6,
        minLineLength=_DOOR_LEAF_MIN,
        maxLineGap=3,
    )
    return lines is not None


def _looks_like_stairs(mask: np.ndarray) -> bool:
    """Reject components made of many parallel short lines (staircases)."""
    lines = cv2.HoughLinesP(
        mask, 1, np.pi / 180, threshold=10, minLineLength=8, maxLineGap=2
    )
    return lines is not None and len(lines) >= 10


def _looks_like_fixture(mask: np.ndarray) -> bool:
    """Reject denser, larger components that are clearly fixtures."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    c = max(contours, key=cv2.contourArea)
    _x, _y, w, h = cv2.boundingRect(c)
    area = cv2.contourArea(c)
    fill = area / (w * h + 1e-6)
    return (w > 35 and h > 18 and area > 120) or fill > 0.45


def _crop_component(mask: np.ndarray) -> tuple[np.ndarray | None, tuple[int, int, int, int] | None]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None, None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return mask[y0 : y1 + 1, x0 : x1 + 1].copy(), (x0, y0, x1 + 1, y1 + 1)


def _classify_door(component_mask: np.ndarray) -> tuple[bool, dict]:
    """Decide whether a connected component is a door swing.

    Returns (is_door, info) where `info` includes the hinge / radius / start
    angle in the **component-local** coordinate system. Callers must offset
    by the component bbox to get image coords.
    """
    roi, bbox = _crop_component(component_mask)
    if roi is None or bbox is None:
        return False, {}

    h, w = roi.shape
    area = int(np.sum(roi > 0))
    fill = area / (h * w + 1e-6)
    max_dim = max(h, w)
    min_dim = min(h, w)

    if area < 8:
        return False, {}
    if max_dim < _DOOR_MIN_SIZE or max_dim > _DOOR_MAX_SIZE:
        return False, {}
    if fill > _DOOR_MAX_FILL:
        return False, {}

    score, hinge_cx, hinge_cy, radius, start_deg = _best_arc_score(roi)
    line_ok = _has_leaf_line(roi)

    # Pass 1: a normal door has both an arc and a straight leaf
    accepted = False
    if score >= _DOOR_ARC_SCORE and line_ok:
        accepted = True
    # Pass 2: arc-only fallback for residual components where the leaf
    # didn't survive the morphology — but reject stairs and fixtures
    elif score >= _DOOR_ARC_ONLY_SCORE:
        if _looks_like_stairs(roi):
            return False, {}
        if _looks_like_fixture(roi):
            return False, {}
        if fill < 0.22 and min_dim >= 6:
            accepted = True

    if not accepted:
        return False, {}

    info = {
        "hinge_local_x": int(hinge_cx),
        "hinge_local_y": int(hinge_cy),
        "swing_radius": int(round(radius)) if radius > 0 else max(min_dim, 8),
        "swing_start_angle": float(start_deg),
        "swing_end_angle": float(start_deg + 90.0),
        "bbox": bbox,
    }
    return True, info


def extract_doors(preprocessed: dict) -> list[dict]:
    """Detect door swing arcs using the arc-band scorer.

    Operates on the residual (non-wall, non-text) ink — first lightly
    re-connects broken arc fragments, then runs the classifier on each
    connected component.
    """
    # Prefer the text-stripped binary BEFORE the outer-shell sealing pass:
    # the sealing pass redraws the building outline as a thick contour, and
    # we don't want that contour leaking into the door residual.
    binary = preprocessed.get("binary_no_text") or preprocessed["binary"]

    # The walls are baked into `binary`; subtract them so we only look at
    # the residual ink that could plausibly be a door arc.
    walls_mask = _wall_mask(binary)
    residual = cv2.subtract(binary, walls_mask)

    # Reconnect broken arc fragments.
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_DOOR_LOCAL_CLOSE, _DOOR_LOCAL_CLOSE)
    )
    work = cv2.morphologyEx(residual, cv2.MORPH_CLOSE, kernel, iterations=1)

    num, labels, stats, _ = cv2.connectedComponentsWithStats(work, connectivity=8)

    doors: list[dict] = []
    for i in range(1, num):
        _x, _y, _w, _h, area = stats[i]
        if area < 8:
            continue

        comp = np.zeros_like(residual)
        comp[labels == i] = 255

        is_door, info = _classify_door(comp)
        if not is_door:
            continue

        bbox = info["bbox"]
        hinge_x = bbox[0] + info["hinge_local_x"]
        hinge_y = bbox[1] + info["hinge_local_y"]
        doors.append(
            {
                "hinge_x": int(hinge_x),
                "hinge_y": int(hinge_y),
                "swing_radius": int(info["swing_radius"]),
                "door_type": "single_swing",
                "swing_start_angle": float(info["swing_start_angle"]),
                "swing_end_angle": float(info["swing_end_angle"]),
            }
        )

    return doors


def _wall_mask(binary: np.ndarray) -> np.ndarray:
    """Quick wall extraction (long horizontal/vertical strokes) for masking
    purposes only — not the source of `extract_walls`'s polished output."""
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    return cv2.bitwise_or(horiz, vert)


def extract_fixtures(preprocessed: dict) -> list[dict]:
    """Detect fixture-shaped contours so Claude can semantically label them."""
    binary = preprocessed["binary"]

    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    fixtures: list[dict] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 50 or area > 10000:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * np.pi * area / (perimeter * perimeter) if perimeter > 0 else 0
        aspect_ratio = float(w) / h if h > 0 else 1.0

        if circularity > 0.7:
            shape = "circle"
        elif 0.8 < aspect_ratio < 1.2:
            shape = "square"
        else:
            shape = "rectangle"

        has_x = _detect_x_pattern(binary, x, y, w, h)
        circle_count = _detect_concentric_circles(binary, x, y, w, h)

        fixtures.append(
            {
                "x": int(x + w / 2),
                "y": int(y + h / 2),
                "width": int(w),
                "height": int(h),
                "shape": shape,
                "circularity": round(circularity, 2),
                "aspect_ratio": round(aspect_ratio, 2),
                "has_x_pattern": has_x,
                "concentric_circles": circle_count,
                "fixture_type": "",
            }
        )

    return fixtures


def _detect_x_pattern(binary, x: int, y: int, w: int, h: int) -> bool:
    """True if a region's diagonals are densely lit (bathtub X)."""
    roi = binary[y : y + h, x : x + w]
    if roi.size == 0:
        return False

    diag1 = 0
    diag2 = 0
    n = min(w, h)
    for i in range(n):
        px1 = int(i * w / n)
        py1 = int(i * h / n)
        px2 = w - 1 - px1
        if 0 <= px1 < w and 0 <= py1 < h and roi[py1, px1] > 128:
            diag1 += 1
        if 0 <= px2 < w and 0 <= py1 < h and roi[py1, px2] > 128:
            diag2 += 1

    return diag1 > n * 0.3 and diag2 > n * 0.3


def _detect_concentric_circles(binary, x: int, y: int, w: int, h: int) -> int:
    """Count circles inside a bounding box (cooktop burners)."""
    roi = binary[y : y + h, x : x + w]
    if roi.size == 0 or min(w, h) < 10:
        return 0

    circles = cv2.HoughCircles(
        cv2.bitwise_not(roi),
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(w, h) // 6 or 1,
        param1=50,
        param2=20,
        minRadius=max(w, h) // 12 or 1,
        maxRadius=max(w, h) // 3 or 1,
    )
    return len(circles[0]) if circles is not None else 0


def _point_in_polygon(px: float, py: float, polygon: list[list[int]]) -> bool:
    """Ray-casting point-in-polygon."""
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside


# ─── Step 3: OCR text extraction ────────────────────────────────────────────


_ROOM_NAMES = {
    "Living", "Bedroom", "Bed", "Kitchen", "Bath", "Bathroom",
    "Closet", "Pantry", "Dining", "Office", "Garage",
    "Laundry", "Entry", "Hall", "Hallway", "Storage", "Master",
}
_FIXTURE_LABELS = {"W/D", "WD", "Pan", "Ref", "DW"}
_DIM_RE = re.compile(r"[0-9]+['\"]|[0-9]+\s*[x×]\s*[0-9]+")


def extract_text_labels(preprocessed: dict) -> list[dict]:
    """Run Tesseract over the grayscale image and group nearby words into labels."""
    if not _HAS_TESSERACT:
        return []

    gray = preprocessed["gray"]
    try:
        ocr_data = pytesseract.image_to_data(
            gray, output_type=pytesseract.Output.DICT, config="--psm 6"
        )
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as err:
        logger.warning(f"Tesseract not available — skipping OCR ({err})")
        return []

    labels: list[dict] = []
    current = {"words": [], "x": 0, "y": 0, "w": 0, "h": 0}

    n_boxes = len(ocr_data["text"])
    for i in range(n_boxes):
        text = (ocr_data["text"][i] or "").strip()
        try:
            conf = int(float(ocr_data["conf"][i]))
        except (ValueError, TypeError):
            conf = -1

        if not text or conf < 40:
            if current["words"]:
                labels.append(_finalize_label(current))
                current = {"words": [], "x": 0, "y": 0, "w": 0, "h": 0}
            continue

        x = int(ocr_data["left"][i])
        y = int(ocr_data["top"][i])
        w = int(ocr_data["width"][i])
        h = int(ocr_data["height"][i])

        if current["words"]:
            gap = x - (current["x"] + current["w"])
            if gap < 30 and abs(y - current["y"]) < 10:
                current["words"].append(text)
                current["w"] = (x + w) - current["x"]
                continue
            labels.append(_finalize_label(current))

        current = {"words": [text], "x": x, "y": y, "w": w, "h": h}

    if current["words"]:
        labels.append(_finalize_label(current))

    return labels


def _finalize_label(label_data: dict) -> dict:
    text = " ".join(label_data["words"])

    label_type = "text"
    if _DIM_RE.search(text):
        label_type = "dimensions"
    elif text.split()[0] in _ROOM_NAMES if text else False:
        label_type = "room_name"
    elif text in _FIXTURE_LABELS:
        label_type = "fixture_label"
    elif "Floor Plan" in text or text.endswith("Plan"):
        label_type = "title"

    return {
        "text": text,
        "x": label_data["x"],
        "y": label_data["y"],
        "width": label_data["w"],
        "height": label_data["h"],
        "center_x": label_data["x"] + label_data["w"] // 2,
        "center_y": label_data["y"] + label_data["h"] // 2,
        "label_type": label_type,
    }


# ─── Step 4: Semantic labeling (Claude Vision — identification only) ────────


def _draw_numbered_regions(
    original_bgr: np.ndarray, rooms: list[dict], fixtures: list[dict]
) -> str:
    """Annotate the original image with numbered boxes for Claude to identify."""
    annotated = original_bgr.copy()

    for i, room in enumerate(rooms):
        bbox = room["bbox"]
        cv2.rectangle(
            annotated,
            (bbox["x"], bbox["y"]),
            (bbox["x"] + bbox["w"], bbox["y"] + bbox["h"]),
            (255, 100, 100),
            2,
        )
        cv2.putText(
            annotated,
            str(i + 1),
            (bbox["x"] + 5, bbox["y"] + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 100, 100),
            2,
        )

    for i, fixture in enumerate(fixtures):
        x = int(fixture["x"] - fixture["width"] / 2)
        y = int(fixture["y"] - fixture["height"] / 2)
        cv2.rectangle(
            annotated, (x, y), (x + fixture["width"], y + fixture["height"]), (100, 255, 100), 2
        )
        cv2.putText(
            annotated,
            f"F{i + 1}",
            (x + 2, y + 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (100, 255, 100),
            2,
        )

    ok, buffer = cv2.imencode(".png", annotated)
    if not ok:
        raise RuntimeError("Failed to encode annotated PNG")
    return base64.b64encode(buffer).decode("utf-8")


_CLAUDE_LABEL_PROMPT = """Look at this floor plan image. I have numbered several regions and objects with colored boxes.

For each numbered REGION (blue boxes labelled 1, 2, 3...), tell me:
1. What TYPE of room is it? (living_room, bedroom, kitchen, bathroom, closet, hallway, laundry, storage, dining, office, garage, other)
2. What FIXTURES does it contain? List each fixture you can see inside the region.

For each numbered OBJECT/FIXTURE (green boxes labelled F1, F2, F3...), tell me:
1. What TYPE of fixture is it? (toilet, bathtub, shower, sink, kitchen_sink, cooktop, refrigerator, oven, washer, dryer, washer_dryer_stacked, dishwasher, closet_rod, single_swing, sliding, bifold, standard_window, other)

Return ONLY JSON, no markdown:
{
  "rooms": {
    "room_0": {"type": "living_room", "fixtures_inside": []},
    "room_1": {"type": "bedroom", "fixtures_inside": ["closet_rod"]}
  },
  "fixtures": {
    "F1": {"type": "cooktop", "details": "4 burners"},
    "F2": {"type": "toilet", "details": "standard"}
  }
}

CRITICAL: Use the room_id field shown in the system (room_0, room_1, …) — these match the on-image blue numbers in order. For fixtures use F1, F2, … exactly as drawn. Do NOT invent coordinates — I already have precise coordinates from computer vision. I only need IDENTIFICATION."""


def label_with_claude(
    annotated_image_b64: str, rooms: list[dict], fixtures: list[dict]
) -> dict:
    """Ask Claude to identify each numbered region/fixture."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "your-key-here":
        logger.warning("ANTHROPIC_API_KEY not set — skipping semantic labeling")
        return {"rooms": {}, "fixtures": {}}

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    room_id_list = ", ".join(r["id"] for r in rooms) or "(none)"
    fixture_id_list = ", ".join(f"F{i + 1}" for i in range(len(fixtures))) or "(none)"
    context_note = f"\n\nRoom ids in image order: {room_id_list}\nFixture ids in image order: {fixture_id_list}"

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": annotated_image_b64,
                        },
                    },
                    {"type": "text", "text": _CLAUDE_LABEL_PROMPT + context_note},
                ],
            }
        ],
    )

    parsed = _parse_json(response.content[0].text)
    if not parsed:
        return {"rooms": {}, "fixtures": {}}
    return parsed


# ─── Step 5: SVG assembly ───────────────────────────────────────────────────


def assemble_svg(
    walls: list[dict],
    rooms: list[dict],
    doors: list[dict],
    fixtures: list[dict],
    labels: list[dict],
    claude_labels: dict,
    img_width: int,
    img_height: int,
    svg_width: int = 1000,
) -> str:
    """Build the final SVG. Geometry from CV; labels from OCR; types from Claude."""
    svg_height = max(int(svg_width * img_height / img_width), 1)
    scale = svg_width / img_width

    def sx(px: float) -> float:
        return round(px * scale, 1)

    def sy(py: float) -> float:
        return round(py * scale, 1)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {svg_width} {svg_height}" '
        f'width="{svg_width}" height="{svg_height}">',
        SVG_STYLES,
        f'<rect width="{svg_width}" height="{svg_height}" fill="#0B0F19"/>',
    ]

    # 1. Room fills
    for room in rooms:
        polygon = room.get("polygon", [])
        if not polygon:
            continue
        room_type = (
            claude_labels.get("rooms", {}).get(room["id"], {}).get("type")
            or room.get("room_type")
            or "other"
        )
        points = " ".join(f"{sx(p[0])},{sy(p[1])}" for p in polygon)
        parts.append(
            f'<polygon points="{points}" class="room-fill" data-room-type="{room_type}"/>'
        )

    # 2. Fixtures (rendered with the symbol library)
    for i, fixture in enumerate(fixtures):
        fixture_key = f"F{i + 1}"
        claude_info = claude_labels.get("fixtures", {}).get(fixture_key, {})
        fixture_type = claude_info.get("type") or fixture.get("fixture_type")
        if not fixture_type or fixture_type in {"unknown", "other"}:
            continue

        rendered = render_element_to_svg(
            fixture_type,
            {
                "x": sx(fixture["x"]),
                "y": sy(fixture["y"]),
                "width": sx(fixture["width"]),
                "height": sy(fixture["height"]),
                "orientation": 0,
                "fixture_type": fixture_type,
                "burner_count": fixture.get("concentric_circles") or 4,
            },
        )
        parts.append(rendered)

    # 3. Walls
    for wall in walls:
        wall_class = "exterior-wall" if wall["wall_type"] == "exterior" else "interior-wall"
        parts.append(
            f'<line x1="{sx(wall["start_x"])}" y1="{sy(wall["start_y"])}" '
            f'x2="{sx(wall["end_x"])}" y2="{sy(wall["end_y"])}" class="{wall_class}"/>'
        )

    # 4. Doors
    for door in doors:
        hx = sx(door["hinge_x"])
        hy = sy(door["hinge_y"])
        radius = sx(door["swing_radius"])
        # Default door panel: extending right from the hinge.
        parts.append(
            svg_door_swing(
                hx,
                hy,
                hx + radius,
                hy,
                radius,
                door.get("swing_start_angle", 0),
                door.get("swing_end_angle", 90),
            )
        )

    # 5. Text labels (OCR — much more accurate than Claude for printed text)
    for label in labels:
        if label["label_type"] == "title":
            continue
        font_size = 14 if label["label_type"] == "room_name" else 10
        font_weight = "bold" if label["label_type"] == "room_name" else "normal"
        text = (label["text"] or "").replace("&", "&amp;").replace("<", "&lt;")
        parts.append(
            f'<text x="{sx(label["center_x"])}" y="{sy(label["center_y"])}" '
            f'text-anchor="middle" fill="#94A3B8" '
            f'font-family="Inter, sans-serif" font-size="{font_size}" '
            f'font-weight="{font_weight}">{text}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ─── Full hybrid pipeline ───────────────────────────────────────────────────


async def convert_floor_plan_hybrid(image_bytes: bytes) -> dict[str, Any] | None:
    """End-to-end conversion. Returns the same shape as `convert_floor_plan`.

    Returns ``None`` (so the router falls through to the Claude vision
    pipeline) if Tesseract isn't available — without OCR room labels the
    CV-only output is noisier than the pure Claude rendering.
    """
    if not _HAS_TESSERACT:
        logger.info("Skipping hybrid pipeline — Tesseract not installed")
        return None

    try:
        preprocessed = preprocess_floor_plan(image_bytes)
    except Exception as e:
        logger.error(f"Preprocess failed: {e}", exc_info=True)
        return None

    walls = extract_walls(preprocessed)
    rooms = extract_rooms(preprocessed, walls)
    doors = extract_doors(preprocessed)
    fixtures = extract_fixtures(preprocessed)
    labels = extract_text_labels(preprocessed)

    logger.info(
        f"CV pass: {len(walls)} walls, {len(rooms)} rooms, "
        f"{len(doors)} doors, {len(fixtures)} fixtures, {len(labels)} labels"
    )

    # Match OCR labels to rooms by spatial containment.
    for label in labels:
        if not rooms:
            break
        if label["label_type"] in {"room_name", "dimensions"}:
            for room in rooms:
                if _point_in_polygon(label["center_x"], label["center_y"], room["polygon"]):
                    if label["label_type"] == "room_name" and not room["label"]:
                        room["label"] = label["text"]
                    elif label["label_type"] == "dimensions" and not room["dimensions"]:
                        room["dimensions"] = label["text"]
                    break

    # Semantic identification via Claude (no coordinates).
    annotated_b64 = _draw_numbered_regions(preprocessed["original"], rooms, fixtures)
    try:
        claude_labels = label_with_claude(annotated_b64, rooms, fixtures)
    except Exception as e:
        logger.warning(f"Claude semantic pass failed, continuing without: {e}")
        claude_labels = {"rooms": {}, "fixtures": {}}

    # Apply Claude room types onto our room objects.
    for room in rooms:
        rinfo = claude_labels.get("rooms", {}).get(room["id"]) or {}
        if rinfo.get("type"):
            room["room_type"] = rinfo["type"]

    svg = assemble_svg(
        walls,
        rooms,
        doors,
        fixtures,
        labels,
        claude_labels,
        preprocessed["width"],
        preprocessed["height"],
    )

    # Build a structured_data block matching the existing schema so the router
    # and frontend continue to work without changes.
    structured_data = {
        "canvas_width": 1000,
        "canvas_height": int(1000 * preprocessed["height"] / preprocessed["width"]),
        "elements": {
            "walls": walls,
            "rooms": rooms,
            "doors": doors,
            "windows": [],
            "bathroom_fixtures": [],
            "kitchen_fixtures": [],
            "laundry": [],
            "closets": [],
            "stairs": [],
            "furniture": [],
            "equipment": [],
            "zones": [],
            "annotations": [
                {
                    "x": label["center_x"],
                    "y": label["center_y"],
                    "text": label["text"],
                    "font_size": "medium" if label["label_type"] == "room_name" else "small",
                    "annotation_type": label["label_type"],
                }
                for label in labels
            ],
        },
        "metadata": {
            "image_type": "scanned_or_blueprint",
            "image_width": preprocessed["width"],
            "image_height": preprocessed["height"],
            "pipeline": "hybrid_cv_claude",
        },
    }

    metadata = {
        "image_type": "scanned_or_blueprint",
        "pipeline": "hybrid_cv_claude",
        "ocr_available": _HAS_TESSERACT,
        "image_width": preprocessed["width"],
        "image_height": preprocessed["height"],
        "confidence": 0.85,
        "element_counts": {
            "walls": len(walls),
            "rooms": len(rooms),
            "doors": len(doors),
            "fixtures": len(fixtures),
            "labels": len(labels),
        },
        "recognized_symbols": _recognized_symbols_from_claude(claude_labels),
    }

    corrections: list[str] = []
    if not _HAS_TESSERACT:
        corrections.append("Tesseract OCR not installed — text labels were skipped")
    if not claude_labels.get("rooms") and not claude_labels.get("fixtures"):
        corrections.append("Claude semantic pass returned no labels — fixtures may be unlabeled")

    return {
        "svg": svg,
        "structured_data": structured_data,
        "metadata": metadata,
        "corrections": corrections,
    }


def _recognized_symbols_from_claude(claude_labels: dict) -> dict[str, int]:
    """Roll up Claude's per-fixture types into a {type: count} dict."""
    counts: dict[str, int] = {}
    for info in (claude_labels.get("fixtures") or {}).values():
        t = info.get("type")
        if not t or t == "other":
            continue
        key = f"{t}s" if not t.endswith("s") else t
        counts[key] = counts.get(key, 0) + 1
    for info in (claude_labels.get("rooms") or {}).values():
        t = info.get("type")
        if not t:
            continue
        counts[f"rooms_{t}"] = counts.get(f"rooms_{t}", 0) + 1
    return counts
