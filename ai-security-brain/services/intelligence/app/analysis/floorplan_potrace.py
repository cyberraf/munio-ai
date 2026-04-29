"""Potrace-based floor plan tracer.

For high-contrast / clean blueprints, `potrace` produces a geometrically
perfect vector tracing of the wall outlines. We then layer the OCR text
labels and Claude semantic identifications over the trace using the same
helpers from `floorplan_hybrid`.

If the `potrace` binary isn't on PATH or the trace fails, this module
returns `None` and the router falls through to the hybrid CV pipeline.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.analysis.floorplan_hybrid import (
    SVG_STYLES,
    _draw_numbered_regions,
    _point_in_polygon,
    _recognized_symbols_from_claude,
    extract_doors,
    extract_fixtures,
    extract_rooms,
    extract_text_labels,
    label_with_claude,
    preprocess_floor_plan,
)
from app.maps.svg_symbols import render_element_to_svg, svg_door_swing

logger = logging.getLogger("intelligence.analysis.floorplan_potrace")


def _potrace_available() -> bool:
    return shutil.which("potrace") is not None


def trace_to_svg_with_potrace(binary_image: np.ndarray) -> str | None:
    """Convert a binary image to SVG paths via the potrace CLI.

    Potrace expects black-on-white (foreground = black). Our binary images
    come out of `preprocess_floor_plan` as white-walls-on-black, so we
    invert before writing the temp BMP.
    """
    if not _potrace_available():
        logger.warning("potrace binary not on PATH — skipping vector trace")
        return None

    inverted = cv2.bitwise_not(binary_image)

    with tempfile.TemporaryDirectory() as tmp:
        bmp_path = Path(tmp) / "floor_plan.bmp"
        svg_path = Path(tmp) / "floor_plan.svg"

        if not cv2.imwrite(str(bmp_path), inverted):
            logger.error("Failed to write BMP for potrace")
            return None

        try:
            subprocess.run(
                [
                    "potrace",
                    str(bmp_path),
                    "-s",  # SVG output
                    "-o",
                    str(svg_path),
                    "--turdsize",
                    "5",
                    "--alphamax",
                    "1.0",
                    "--opttolerance",
                    "0.2",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(f"potrace failed: {e}")
            return None

        if not svg_path.exists():
            logger.error("potrace produced no output")
            return None

        return svg_path.read_text(encoding="utf-8")


# ─── SVG composition ────────────────────────────────────────────────────────


_VIEWBOX_RE = re.compile(r'viewBox="([^"]+)"')
_PATH_RE = re.compile(r"<path\b[^>]*>", re.IGNORECASE)


def _extract_potrace_paths(potrace_svg: str) -> tuple[str, tuple[float, float, float, float]]:
    """Extract the inner <path> elements + viewBox from potrace's SVG output."""
    vb_match = _VIEWBOX_RE.search(potrace_svg)
    if vb_match:
        nums = [float(n) for n in vb_match.group(1).split()]
        viewbox = (nums[0], nums[1], nums[2], nums[3]) if len(nums) >= 4 else (0.0, 0.0, 1.0, 1.0)
    else:
        viewbox = (0.0, 0.0, 1.0, 1.0)

    # potrace wraps everything in <g transform="translate(…) scale(…)"> so we
    # keep the whole <g>…</g> block. Strip the outer <svg> wrapper.
    g_start = potrace_svg.find("<g")
    g_end = potrace_svg.rfind("</g>")
    if g_start == -1 or g_end == -1:
        return "", viewbox

    inner = potrace_svg[g_start : g_end + len("</g>")]
    # Force every path to use the exterior-wall stroke so they match the theme.
    inner = _PATH_RE.sub(
        lambda m: m.group(0).rstrip(">")
        + ' fill="#E8ECF1" stroke="#E8ECF1" stroke-width="0">',
        inner,
    )
    return inner, viewbox


def _assemble_potrace_svg(
    potrace_svg: str,
    rooms: list[dict],
    doors: list[dict],
    fixtures: list[dict],
    labels: list[dict],
    claude_labels: dict,
    img_width: int,
    img_height: int,
    svg_width: int = 1000,
) -> str:
    """Compose the final SVG: potrace walls + CV/OCR/Claude annotations."""
    svg_height = max(int(svg_width * img_height / img_width), 1)
    scale = svg_width / img_width

    def sx(px: float) -> float:
        return round(px * scale, 1)

    def sy(py: float) -> float:
        return round(py * scale, 1)

    paths_block, vb = _extract_potrace_paths(potrace_svg)

    # potrace's coordinate system is bottom-left-origin and may differ from
    # our top-left-origin viewBox. Wrap it in a <g> that rescales to our SVG.
    _, _, vb_w, vb_h = vb
    if vb_w <= 0 or vb_h <= 0:
        sx_pot = sy_pot = 1.0
        offset_y = 0.0
    else:
        sx_pot = svg_width / vb_w
        sy_pot = svg_height / vb_h
        offset_y = 0.0

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
        parts.append(
            render_element_to_svg(
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
        )

    # 3. Walls — the actual potrace vector trace
    if paths_block:
        parts.append(
            f'<g transform="translate(0,{offset_y}) scale({sx_pot},{sy_pot})">'
            f"{paths_block}"
            f"</g>"
        )

    # 4. Doors (CV-detected, drawn on top of the trace)
    for door in doors:
        hx = sx(door["hinge_x"])
        hy = sy(door["hinge_y"])
        radius = sx(door["swing_radius"])
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

    # 5. OCR labels
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


# ─── Full pipeline ──────────────────────────────────────────────────────────


async def convert_floor_plan_potrace(image_bytes: bytes) -> dict[str, Any] | None:
    """Trace walls with potrace, layer OCR + Claude semantics on top."""
    if not _potrace_available():
        return None

    try:
        preprocessed = preprocess_floor_plan(image_bytes)
    except Exception as e:
        logger.error(f"Preprocess failed: {e}", exc_info=True)
        return None

    # Use the cleaner Otsu binary for potrace — adaptive threshold tends to
    # produce noisy strokes that bloat the trace.
    binary_for_trace = preprocessed.get("binary_simple", preprocessed["binary"])
    potrace_svg = trace_to_svg_with_potrace(binary_for_trace)
    if not potrace_svg:
        return None

    rooms = extract_rooms(preprocessed, [])
    doors = extract_doors(preprocessed)
    fixtures = extract_fixtures(preprocessed)
    labels = extract_text_labels(preprocessed)

    logger.info(
        f"Potrace pass: trace={len(potrace_svg)}B, "
        f"{len(rooms)} rooms, {len(doors)} doors, "
        f"{len(fixtures)} fixtures, {len(labels)} labels"
    )

    # Match OCR room names / dimensions to rooms by spatial containment.
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

    annotated_b64 = _draw_numbered_regions(preprocessed["original"], rooms, fixtures)
    try:
        claude_labels = label_with_claude(annotated_b64, rooms, fixtures)
    except Exception as e:
        logger.warning(f"Claude semantic pass failed, continuing without: {e}")
        claude_labels = {"rooms": {}, "fixtures": {}}

    for room in rooms:
        rinfo = claude_labels.get("rooms", {}).get(room["id"]) or {}
        if rinfo.get("type"):
            room["room_type"] = rinfo["type"]

    svg = _assemble_potrace_svg(
        potrace_svg,
        rooms,
        doors,
        fixtures,
        labels,
        claude_labels,
        preprocessed["width"],
        preprocessed["height"],
    )

    structured_data = {
        "canvas_width": 1000,
        "canvas_height": int(1000 * preprocessed["height"] / preprocessed["width"]),
        "elements": {
            "walls": [],  # walls are baked into the potrace path block
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
            "image_type": "blueprint",
            "image_width": preprocessed["width"],
            "image_height": preprocessed["height"],
            "pipeline": "potrace_cv_claude",
        },
    }

    metadata = {
        "image_type": "blueprint",
        "pipeline": "potrace_cv_claude",
        "image_width": preprocessed["width"],
        "image_height": preprocessed["height"],
        "confidence": 0.92,
        "element_counts": {
            "rooms": len(rooms),
            "doors": len(doors),
            "fixtures": len(fixtures),
            "labels": len(labels),
            "trace_bytes": len(potrace_svg),
        },
        "recognized_symbols": _recognized_symbols_from_claude(claude_labels),
    }

    corrections: list[str] = []
    if not claude_labels.get("rooms") and not claude_labels.get("fixtures"):
        corrections.append("Claude semantic pass returned no labels — fixtures may be unlabeled")

    return {
        "svg": svg,
        "structured_data": structured_data,
        "metadata": metadata,
        "corrections": corrections,
    }
