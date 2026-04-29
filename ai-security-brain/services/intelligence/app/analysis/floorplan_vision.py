"""Floor plan image → structured JSON → SVG pipeline using Claude Vision.

Pipeline:
1. Preprocess image (resize, contrast, sharpen)
2. Claude analyzes the image and returns structured JSON describing every element
3. Second Claude pass verifies the JSON against the original image
4. JSON is converted to a styled SVG matching the ASB dark theme
"""

import base64
import io
import json
import logging
import re
from pathlib import Path

import anthropic
from PIL import Image, ImageEnhance, ImageFilter

from app.config import settings
from app.maps.svg_symbols import render_element_to_svg

logger = logging.getLogger("intelligence.analysis.floorplan_vision")

# ─── Prompts ────────────────────────────────────────────────────────────────

FLOOR_PLAN_SYSTEM_PROMPT = """You are an expert architectural analyst and SVG engineer. Your job is to analyze floor plan images and produce an extremely accurate structured JSON description of every element in the floor plan.

CRITICAL ACCURACY RULES:
- Measure proportions precisely. If a wall is 3x longer than another wall, the output must reflect that exact ratio.
- Maintain spatial relationships exactly. If Room A is to the left of Room B, and Room C is above both, the coordinates must reflect this precisely.
- Preserve scale consistency. All elements must be sized relative to each other accurately.
- Count precisely. If there are 6 shelving rows, output exactly 6. Not 5, not 7.
- Angles matter. If a wall is at 45 degrees, output 45 degrees. If it's 90 degrees, output 90 degrees exactly.
- Do NOT invent elements that are not visible in the image.
- Do NOT omit elements that ARE visible in the image.
- If you're uncertain about an element, include it with a confidence score below 0.7.

VISUAL READING CONVENTIONS:
- Solid black lines = walls (thicker = exterior, thinner = interior)
- Black dashed arcs/curves = door swings (the door pivots along the arc)
- Black filled shapes = fixtures (toilets, sinks, stoves, washers, beds, sofas)
- Text labels = room names and dimensions

OUTPUT FORMAT:
Return ONLY valid JSON matching the schema. No explanation, no markdown, no preamble."""


FLOOR_PLAN_USER_PROMPT = """Analyze this floor plan image with extreme precision. You must recognize standard architectural drawing symbols and extract every element.

COORDINATE SYSTEM:
- Set canvas width to 1000 units. Calculate height from the image aspect ratio.
- Origin (0,0) is top-left. X increases rightward. Y increases downward.
- ALL coordinates must be in this 0-1000 scale.

ARCHITECTURAL SYMBOL RECOGNITION GUIDE:
You must identify these standard floor plan symbols. Study each carefully:

## DOORS
- SINGLE SWING DOOR: A straight line (the door panel) with a quarter-circle arc showing the swing direction. The arc sweeps 90°. The hinge point is where the door meets the wall. Record: hinge position, swing radius, swing direction (inward/outward, left/right).
- DOUBLE SWING DOOR: Two quarter-circle arcs mirrored, forming a half-circle or two separate arcs. Two door panels.
- SLIDING DOOR: Two overlapping rectangles or parallel lines on a track. Often shown as two thin rectangles that slide past each other. Common for closets and patios.
- POCKET DOOR: A single rectangle shown receding into the wall (drawn partially inside the wall thickness).
- BIFOLD DOOR: A zigzag or accordion pattern, usually for closets. Looks like connected V shapes.
- FRENCH DOOR: Two swing doors side by side, each with an arc, opening from center.
- ROLLUP / GARAGE DOOR: Multiple horizontal lines stacked (like slats). Usually wider than standard doors.
- OPEN DOORWAY: A gap in the wall with no door symbol — just the opening.

## WINDOWS
- STANDARD WINDOW: Two or three parallel lines within the wall thickness. The lines run along the wall direction. Width varies.
- SLIDING WINDOW: Similar to standard but with an arrow or offset indicating the sliding panel.
- BAY WINDOW: A window that protrudes outward from the wall, forming an angled or curved bump.
- PICTURE WINDOW: A single large pane, shown as one line or a filled rectangle in the wall.

## BATHROOM FIXTURES
- TOILET: An oval or elongated U shape attached to a wall or small rectangle (tank). The tank is the rectangular part against the wall, the bowl is the oval extending outward. Top-down view.
- BATHTUB: A large rectangle with an X inside (drain cross), or a rectangle with rounded corners. Usually the largest fixture in the bathroom. Occupies most of one wall.
- SHOWER: A square or rectangle with a small circle inside (shower head/drain) and sometimes a door line or curtain indicator. May have tile pattern (grid lines).
- SINK (bathroom): A small oval or circle, often with a small rectangle behind it (vanity/counter). Smaller than a toilet.
- DOUBLE VANITY: Two circles/ovals side by side on a rectangular counter.

## KITCHEN FIXTURES
- STOVE / COOKTOP: 4 circles arranged in a 2x2 grid (representing burners). Sometimes 5 circles (1 center + 4 corners). Usually located against a wall.
- OVEN: A rectangle below or integrated with the cooktop, sometimes with a smaller rectangle inside.
- REFRIGERATOR: A large rectangle with a circle inside (representing the door handle), or a simple filled rectangle. Usually in a corner.
- KITCHEN SINK: A rectangle with one or two circles/ovals inside (single or double basin). Located on a counter along a wall.
- DISHWASHER: A rectangle next to the sink, sometimes labeled "DW". About 24 inches (60cm) wide.
- MICROWAVE: A small rectangle, often above the stove or on a counter. May be labeled.
- ISLAND / COUNTER: A freestanding rectangular shape not attached to walls. May have stools indicated (circles on one side).
- PANTRY: A room or closet labeled "Pan" or "Pantry", often with shelving lines inside.

## LAUNDRY
- WASHER: A circle inside a square (front-load) or a circle in a rectangle (top-load). Sometimes labeled "W" or "W/D".
- DRYER: Same as washer, usually paired next to it. Sometimes labeled "D".
- STACKED WASHER/DRYER: A single square/rectangle labeled "W/D" — indicates stacked unit.

## CLOSET INDICATORS
- CLOSET ROD: A horizontal line with a curved hook or zigzag pattern below it (representing hanging clothes). The zigzag/sawtooth pattern means clothing hangers.
- SHELF: Horizontal lines within a closet space.
- WALK-IN CLOSET: A separate room-like space, often with rod lines on 2-3 walls.
- REACH-IN CLOSET: Narrow space with bifold or sliding doors, rod line inside.

## STAIRS
- STAIRCASE: Parallel horizontal lines (treads) within a rectangular outline, with an arrow indicating the direction of ascent. "UP" or "DN" labels.
- SPIRAL STAIRCASE: A circle with radiating lines from center (like a pie chart or fan).

## HVAC / UTILITIES
- AC UNIT: A rectangle on an exterior wall, sometimes with fins/lines.
- FURNACE: A rectangle, often in a utility closet, labeled "FURN" or with a flame symbol.
- WATER HEATER: A circle, often in a utility closet or garage.
- ELECTRICAL PANEL: A small rectangle on a wall, sometimes labeled "EP" or with a lightning symbol.

## FURNITURE (if shown)
- BED: A rectangle with a smaller rectangle at one end (headboard/pillow).
- DESK: An L-shape or rectangle, sometimes with a chair circle.
- DINING TABLE: Rectangle or circle with smaller circles around it (chairs).
- SOFA / COUCH: A long rectangle with a thicker back edge.
- BOOKSHELF: A rectangle with internal horizontal lines (shelves).

## OTHER SYMBOLS
- COLUMN / PILLAR: A small filled square or circle, structural.
- FIREPLACE: A rectangle protruding from a wall with a small indentation (hearth opening).
- BALCONY / DECK: An area outside the main walls, often with a railing (parallel line offset from edge).
- ELEVATOR: A square with an X inside or diagonal lines, labeled "ELEV".

EXTRACTION FORMAT:

Return JSON with these categories:

{
  "canvas_width": 1000,
  "canvas_height": <from aspect ratio>,
  "elements": {
    "walls": [
      {
        "start_x": 0, "start_y": 0,
        "end_x": 1000, "end_y": 0,
        "thickness": 12,
        "wall_type": "exterior"
      }
    ],
    "doors": [
      {
        "x": 500, "y": 100,
        "hinge_x": 490, "hinge_y": 100,
        "swing_end_x": 490, "swing_end_y": 160,
        "swing_radius": 60,
        "swing_start_angle": 0,
        "swing_end_angle": 90,
        "swing_direction": "inward_left",
        "door_type": "single_swing",
        "width": 60,
        "wall_gap": true
      }
    ],
    "windows": [
      {
        "start_x": 200, "start_y": 0,
        "end_x": 350, "end_y": 0,
        "width": 150,
        "window_type": "standard",
        "wall_side": "top"
      }
    ],
    "bathroom_fixtures": [
      {
        "x": 600, "y": 700,
        "width": 35, "height": 50,
        "fixture_type": "toilet",
        "orientation": 180,
        "label": ""
      },
      {
        "x": 500, "y": 750,
        "width": 70, "height": 150,
        "fixture_type": "bathtub",
        "orientation": 0,
        "has_x_pattern": true
      },
      {
        "x": 700, "y": 700,
        "width": 25, "height": 20,
        "fixture_type": "sink",
        "orientation": 180
      }
    ],
    "kitchen_fixtures": [
      {
        "x": 100, "y": 50,
        "width": 60, "height": 60,
        "fixture_type": "cooktop",
        "burner_count": 4,
        "orientation": 0
      },
      {
        "x": 100, "y": 200,
        "width": 70, "height": 35,
        "fixture_type": "sink",
        "basin_count": 2,
        "orientation": 0
      },
      {
        "x": 50, "y": 130,
        "width": 60, "height": 70,
        "fixture_type": "refrigerator",
        "orientation": 90
      }
    ],
    "laundry": [
      {
        "x": 100, "y": 850,
        "width": 50, "height": 50,
        "fixture_type": "washer_dryer_stacked",
        "label": "W/D"
      }
    ],
    "closets": [
      {
        "x": 800, "y": 500,
        "width": 100, "height": 60,
        "closet_type": "reach_in",
        "has_rod": true,
        "has_hangers": true,
        "door_type": "bifold",
        "label": "Closet"
      }
    ],
    "stairs": [],
    "furniture": [],
    "rooms": [
      {
        "id": "living",
        "label": "Living",
        "dimensions_text": "7' x 13'0\\"",
        "polygon": [[0,0]],
        "room_type": "living_room"
      }
    ],
    "zones": [],
    "annotations": [
      {
        "x": 300, "y": 150,
        "text": "A6 Floor Plan",
        "font_size": "large",
        "annotation_type": "title"
      }
    ]
  },
  "metadata": {
    "image_type": "blueprint",
    "estimated_dimensions": "approximately 25' x 20' based on bedroom dimensions",
    "confidence": 0.93,
    "recognized_symbols": {
      "doors_swing": 3,
      "doors_bifold": 1,
      "windows": 2,
      "toilets": 1,
      "bathtubs": 1,
      "sinks": 2,
      "cooktops": 1,
      "refrigerators": 1,
      "washer_dryers": 1,
      "closet_rods": 1
    }
  }
}

CRITICAL RULES:
1. For DOORS: You MUST identify the hinge point, the swing arc endpoint, the swing radius, and the direction. The arc in the floor plan IS the swing — trace it precisely.
2. For WINDOWS: Identify every set of parallel lines within walls. These are windows. Report their exact start and end positions along the wall.
3. For FIXTURES: Match them to the symbol guide above. A 2x2 grid of circles = cooktop. An oval near a wall = toilet. A rectangle with an X = bathtub.
4. For CLOSETS: Look for the zigzag/sawtooth pattern (hangers), rod lines, and the door type (bifold = zigzag door, sliding = parallel offset lines).
5. LABEL EVERYTHING you can identify. If the image has text labels ("Living", "Bath", "Closet", "W/D", "Pan"), include them.
6. Report DIMENSIONS if visible (e.g., "12'4\\" x 10'7\\"" for the bedroom).
7. Count every instance: if there are 4 burners, say 4. If there are 2 sinks, say 2. If there are 3 doors, describe all 3.
"""


VERIFICATION_PROMPT = """I previously analyzed a floor plan and produced this JSON description:

{previous_json}

Now look at the original floor plan image again and verify the accuracy of my analysis.

Check:
1. WALL COUNT: Are all walls represented? Are there walls in the JSON that don't exist in the image?
2. ROOM COUNT: Does the number of rooms/areas match what's visible?
3. PROPORTIONS: Are the relative sizes correct? Is the longest wall actually the longest in the image?
4. SPATIAL LAYOUT: Is room A actually to the left of room B as described?
5. EQUIPMENT: Are all shelving rows, desks, machines represented? Count them in the image and compare.
6. DOORS: Are all openings captured?
7. LABELS: Do the text labels match what's visible in the image?

Return a corrected JSON with the SAME schema. If everything is correct, return the same JSON.
If corrections are needed, make them and add a "corrections" field listing what you changed:

{{
  ...same schema...,
  "corrections": [
    "Added missing wall between room_3 and room_4",
    "Fixed shelving_row count from 5 to 6",
    "Adjusted room_2 width ratio"
  ]
}}

Return ONLY the JSON. No markdown, no explanation."""


REFINE_PROMPT = """You previously analyzed a floor plan as JSON. Here it is:

{previous_json}

The user has reviewed your output and given this feedback:
{feedback}

Re-examine the original image, apply the user's feedback, and return a corrected JSON with the SAME schema.
Add a "corrections" field listing what you changed based on the feedback.

Return ONLY the JSON. No markdown, no explanation."""


# ─── Image preprocessing ────────────────────────────────────────────────────


def preprocess_floor_plan(image_bytes: bytes) -> str:
    """Preprocess the floor plan image for optimal Claude Vision analysis.

    Returns base64-encoded PNG.
    """
    img = Image.open(io.BytesIO(image_bytes))

    if img.mode != "RGB":
        img = img.convert("RGB")

    # Resize to max 2000px on longest side
    max_dim = 2000
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

    # Enhance contrast (helps with faded blueprints)
    img = ImageEnhance.Contrast(img).enhance(1.3)

    # Sharpen (helps with scanned documents)
    img = img.filter(ImageFilter.SHARPEN)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")


# ─── Claude API calls ───────────────────────────────────────────────────────


def _strip_markdown(text: str) -> str:
    """Remove markdown code fences from a response if present."""
    text = text.strip()
    if text.startswith("```"):
        # Strip opening fence (and optional language)
        text = re.sub(r"^```\w*\n", "", text)
        # Strip closing fence
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict | None:
    """Parse JSON from Claude's response, handling markdown wrapping."""
    cleaned = _strip_markdown(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Try to extract JSON from anywhere in the text
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.error(f"Failed to parse JSON: {e}")
        logger.debug(f"Raw text: {text[:500]}")
        return None


async def analyze_floor_plan(image_b64: str) -> dict | None:
    """First pass: extract structured JSON from the floor plan image."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "your-key-here":
        logger.warning("ANTHROPIC_API_KEY not set")
        return None

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    logger.info("Analyzing floor plan (pass 1)...")
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        system=FLOOR_PLAN_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": FLOOR_PLAN_USER_PROMPT},
                ],
            }
        ],
    )

    return _parse_json(response.content[0].text)


async def verify_and_refine(image_b64: str, first_pass: dict) -> dict | None:
    """Second pass: verify the analysis against the original image."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "your-key-here":
        return first_pass

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    logger.info("Verifying floor plan analysis (pass 2)...")
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": VERIFICATION_PROMPT.format(
                            previous_json=json.dumps(first_pass, indent=2)
                        ),
                    },
                ],
            }
        ],
    )

    verified = _parse_json(response.content[0].text)
    if not verified:
        logger.warning("Verification pass returned unparseable JSON; using first pass")
        return first_pass

    # Claude often omits metadata when "nothing changed" — merge first-pass metadata
    # as a fallback so confidence/image_type/etc. survive the verification round-trip.
    first_meta = first_pass.get("metadata", {}) or {}
    verified_meta = verified.get("metadata", {}) or {}
    merged_meta = {**first_meta, **{k: v for k, v in verified_meta.items() if v}}
    verified["metadata"] = merged_meta
    return verified


async def refine_with_feedback(image_b64: str, current_data: dict, feedback: str) -> dict | None:
    """Apply user feedback to refine the structured data."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY == "your-key-here":
        return current_data

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    logger.info(f"Refining with user feedback: {feedback[:100]}...")
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": REFINE_PROMPT.format(
                            previous_json=json.dumps(current_data, indent=2),
                            feedback=feedback,
                        ),
                    },
                ],
            }
        ],
    )

    refined = _parse_json(response.content[0].text)
    return refined or current_data


# ─── JSON → SVG conversion ──────────────────────────────────────────────────


SVG_STYLES = """
    <defs>
        <style>
            .exterior-wall { stroke: #E8ECF1; stroke-width: 10; stroke-linecap: round; fill: none; }
            .interior-wall { stroke: #94A3B8; stroke-width: 6; stroke-linecap: round; fill: none; }
            .partition { stroke: #64748B; stroke-width: 3; stroke-dasharray: 8,4; fill: none; }
            .room-fill { fill: #1E293B; stroke: none; opacity: 0.3; }
            .room-label { fill: #E8ECF1; font-family: 'Inter', sans-serif; font-size: 14px; font-weight: bold; text-anchor: middle; }
            .door { stroke: #00C9A7; stroke-width: 3; fill: none; stroke-dasharray: 4,2; }
            .equipment { fill: #4B5563; stroke: #6B7280; stroke-width: 1; }
            .shelving { fill: #4B5563; stroke: #6B7280; stroke-width: 1; }
            .zone-high-traffic { fill: #F59E0B; opacity: 0.10; stroke: #F59E0B; stroke-width: 1; stroke-dasharray: 6,3; }
            .zone-restricted { fill: #EF4444; opacity: 0.08; stroke: #EF4444; stroke-width: 1; stroke-dasharray: 6,3; }
            .zone-charging { fill: #3B82F6; opacity: 0.08; stroke: #3B82F6; stroke-width: 1; stroke-dasharray: 6,3; }
            .zone-loading { fill: #8B5CF6; opacity: 0.08; stroke: #8B5CF6; stroke-width: 1; stroke-dasharray: 6,3; }
            .zone-label { fill: #94A3B8; font-family: 'JetBrains Mono', monospace; font-size: 9px;
                          text-transform: uppercase; letter-spacing: 1px; text-anchor: middle; }
            .annotation { fill: #94A3B8; font-family: 'JetBrains Mono', monospace; font-size: 10px; }
            .column { fill: #4B5563; stroke: #6B7280; stroke-width: 1; }
        </style>
    </defs>
"""


def json_to_svg(plan: dict) -> str:
    """Convert structured floor plan JSON into a styled SVG with accurate symbols."""
    w = plan.get("canvas_width", 1000)
    h = plan.get("canvas_height", 700)
    elements = plan.get("elements", {})

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}">',
        SVG_STYLES,
        f'<rect width="{w}" height="{h}" fill="#0B0F19"/>',
    ]

    # ── 1. Zones (background layer) ──
    for zone in elements.get("zones", []) or []:
        polygon = zone.get("polygon", [])
        if not polygon:
            continue
        points = " ".join(f"{p[0]},{p[1]}" for p in polygon)
        zone_class = f"zone-{zone.get('zone_type', 'restricted').replace('_', '-')}"
        parts.append(f'<polygon points="{points}" class="{zone_class}"/>')

        cx = sum(p[0] for p in polygon) / len(polygon)
        cy = sum(p[1] for p in polygon) / len(polygon)
        label = zone.get("label") or zone.get("zone_type", "").upper().replace("_", " ")
        parts.append(f'<text x="{cx}" y="{cy}" class="zone-label">{label}</text>')

    # ── 2. Rooms (filled areas, no labels yet — labels go on top) ──
    for room in elements.get("rooms", []) or []:
        polygon = room.get("polygon", [])
        if not polygon:
            continue
        points = " ".join(f"{p[0]},{p[1]}" for p in polygon)
        parts.append(f'<polygon points="{points}" class="room-fill"/>')

    # ── 3. Fixtures and equipment (below walls so walls can mask edges) ──
    for fixture in elements.get("kitchen_fixtures", []) or []:
        ftype = fixture.get("fixture_type", "")
        if ftype == "sink":
            ftype = "kitchen_sink"
        parts.append(render_element_to_svg(ftype, fixture))

    for fixture in elements.get("bathroom_fixtures", []) or []:
        parts.append(render_element_to_svg(fixture.get("fixture_type", ""), fixture))

    for item in elements.get("laundry", []) or []:
        parts.append(render_element_to_svg(item.get("fixture_type", "washer_dryer"), item))

    for closet in elements.get("closets", []) or []:
        if closet.get("has_rod"):
            parts.append(render_element_to_svg("closet_rod", closet))

    for stair in elements.get("stairs", []) or []:
        parts.append(render_element_to_svg("staircase", stair))

    # Legacy `equipment` (warehouse shelving etc.) — fall back to simple rect
    for eq in elements.get("equipment", []) or []:
        ex, ey = eq.get("x", 0), eq.get("y", 0)
        ew, eh = eq.get("width", 20), eq.get("height", 20)
        x = ex - ew / 2
        y = ey - eh / 2
        rotation = eq.get("rotation", 0)
        eq_type = eq.get("element_type", "")
        eq_class = "shelving" if "shelv" in eq_type or "rack" in eq_type else "equipment"

        if rotation:
            parts.append(
                f'<rect x="{x}" y="{y}" width="{ew}" height="{eh}" rx="2" '
                f'class="{eq_class}" transform="rotate({rotation},{ex},{ey})"/>'
            )
        else:
            parts.append(
                f'<rect x="{x}" y="{y}" width="{ew}" height="{eh}" rx="2" class="{eq_class}"/>'
            )

        label = eq.get("label", "")
        if label:
            parts.append(
                f'<text x="{ex}" y="{ey + eh/2 + 12}" class="annotation" text-anchor="middle">{label}</text>'
            )

    # ── 4. Walls ──
    for wall in elements.get("walls", []) or []:
        wall_class = {
            "exterior": "exterior-wall",
            "interior": "interior-wall",
            "partition": "partition",
        }.get(wall.get("wall_type", "interior"), "interior-wall")

        parts.append(
            f'<line x1="{wall.get("start_x", 0)}" y1="{wall.get("start_y", 0)}" '
            f'x2="{wall.get("end_x", 0)}" y2="{wall.get("end_y", 0)}" class="{wall_class}"/>'
        )

    # ── 5. Windows (cut into walls) ──
    for window in elements.get("windows", []) or []:
        wtype = window.get("window_type", "standard") + "_window"
        parts.append(render_element_to_svg(wtype, window))

    # ── 6. Doors (on top of walls) ──
    for door in elements.get("doors", []) or []:
        dtype = door.get("door_type", "single_swing")
        if dtype == "opening":
            continue
        parts.append(render_element_to_svg(dtype, door))

    # ── 7. Annotations ──
    size_map = {"small": 9, "medium": 11, "large": 14}
    for ann in elements.get("annotations", []) or []:
        font_size = size_map.get(ann.get("font_size", "small"), 10)
        text = ann.get("text", "")
        if not text:
            continue
        parts.append(
            f'<text x="{ann.get("x", 0)}" y="{ann.get("y", 0)}" class="annotation" '
            f'font-size="{font_size}px">{text}</text>'
        )

    # ── 8. Room labels (centered, top-most) ──
    for room in elements.get("rooms", []) or []:
        polygon = room.get("polygon", [])
        if not polygon:
            continue
        cx = sum(p[0] for p in polygon) / len(polygon)
        cy = sum(p[1] for p in polygon) / len(polygon)
        label = room.get("label", "")
        if label:
            parts.append(f'<text x="{cx}" y="{cy}" class="room-label">{label}</text>')
        dim_text = room.get("dimensions_text", "")
        if dim_text:
            parts.append(
                f'<text x="{cx}" y="{cy + 14}" class="annotation" '
                f'text-anchor="middle" font-size="9px">{dim_text}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


# ─── Deterministic counting helpers ─────────────────────────────────────────


def _compute_element_counts(elements: dict) -> dict:
    """Compute high-level category counts from the elements dict."""
    return {
        "walls": len(elements.get("walls", []) or []),
        "rooms": len(elements.get("rooms", []) or []),
        "doors": len(elements.get("doors", []) or []),
        "windows": len(elements.get("windows", []) or []),
        "bathroom_fixtures": len(elements.get("bathroom_fixtures", []) or []),
        "kitchen_fixtures": len(elements.get("kitchen_fixtures", []) or []),
        "laundry": len(elements.get("laundry", []) or []),
        "closets": len(elements.get("closets", []) or []),
        "stairs": len(elements.get("stairs", []) or []),
        "furniture": len(elements.get("furniture", []) or []),
        "equipment": len(elements.get("equipment", []) or []),
        "zones": len(elements.get("zones", []) or []),
    }


def _compute_recognized_symbols(elements: dict) -> dict:
    """Compute fine-grained symbol counts (door types, fixture types, etc.)."""
    counts: dict[str, int] = {}

    def _bump(key: str, n: int = 1) -> None:
        if n:
            counts[key] = counts.get(key, 0) + n

    for door in elements.get("doors", []) or []:
        dtype = door.get("door_type", "single_swing")
        _bump(f"doors_{dtype}")

    _bump("windows", len(elements.get("windows", []) or []))

    for fx in elements.get("bathroom_fixtures", []) or []:
        ft = fx.get("fixture_type", "fixture")
        _bump(f"{ft}s" if not ft.endswith("s") else ft)

    for fx in elements.get("kitchen_fixtures", []) or []:
        ft = fx.get("fixture_type", "fixture")
        if ft == "sink":
            ft = "kitchen_sink"
        _bump(f"{ft}s" if not ft.endswith("s") else ft)

    for ld in elements.get("laundry", []) or []:
        ft = ld.get("fixture_type", "washer_dryer")
        _bump(f"{ft}s" if not ft.endswith("s") else ft)

    rod_count = sum(1 for c in (elements.get("closets", []) or []) if c.get("has_rod"))
    _bump("closet_rods", rod_count)

    for st in elements.get("stairs", []) or []:
        _bump("staircases")

    return counts


# ─── Full pipeline ──────────────────────────────────────────────────────────


async def convert_floor_plan(image_bytes: bytes) -> dict | None:
    """Full pipeline: image → preprocess → Claude analysis → verification → SVG.

    Returns:
        {
            "svg": "<svg>...</svg>",
            "structured_data": {...},
            "metadata": {...},
            "corrections": [...],
        }
    """
    try:
        # 1. Preprocess
        image_b64 = preprocess_floor_plan(image_bytes)
        logger.info(f"Preprocessed image ({len(image_b64)} b64 chars)")

        # 2. First pass analysis
        first_pass = await analyze_floor_plan(image_b64)
        if not first_pass:
            return None

        # 3. Verification pass
        verified = await verify_and_refine(image_b64, first_pass)
        if not verified:
            verified = first_pass

        # 4. Always compute element counts deterministically from the arrays —
        #    Claude's self-reported counts are unreliable.
        elements = verified.get("elements", {}) or {}
        meta = verified.get("metadata", {}) or {}
        meta["element_counts"] = _compute_element_counts(elements)
        meta["recognized_symbols"] = _compute_recognized_symbols(elements)
        # Default confidence if Claude omitted it
        if "confidence" not in meta or meta.get("confidence") is None:
            meta["confidence"] = 0.75
        verified["metadata"] = meta

        logger.info(
            f"Conversion done — counts={meta['element_counts']} "
            f"confidence={meta.get('confidence')} "
            f"corrections={len(verified.get('corrections', []) or [])}"
        )

        # 5. Convert to SVG
        svg_string = json_to_svg(verified)

        return {
            "svg": svg_string,
            "structured_data": verified,
            "metadata": meta,
            "corrections": verified.get("corrections", []) or [],
        }
    except Exception as e:
        logger.error(f"Floor plan conversion failed: {e}", exc_info=True)
        return None


async def refine_floor_plan(image_bytes: bytes, current_data: dict, feedback: str) -> dict | None:
    """Apply user feedback and regenerate the SVG."""
    try:
        image_b64 = preprocess_floor_plan(image_bytes)
        refined = await refine_with_feedback(image_b64, current_data, feedback)
        if not refined:
            return None

        elements = refined.get("elements", {}) or {}
        prev_meta = current_data.get("metadata", {}) or {}
        new_meta = refined.get("metadata", {}) or {}
        meta = {**prev_meta, **{k: v for k, v in new_meta.items() if v}}
        meta["element_counts"] = _compute_element_counts(elements)
        meta["recognized_symbols"] = _compute_recognized_symbols(elements)
        if "confidence" not in meta or meta.get("confidence") is None:
            meta["confidence"] = 0.75
        refined["metadata"] = meta

        svg_string = json_to_svg(refined)
        return {
            "svg": svg_string,
            "structured_data": refined,
            "metadata": meta,
            "corrections": refined.get("corrections", []) or [],
        }
    except Exception as e:
        logger.error(f"Floor plan refinement failed: {e}", exc_info=True)
        return None


# ─── Legacy file-path wrappers (used by router) ─────────────────────────────


async def convert_and_save(image_path: str, output_dir: str) -> str | None:
    """Convert a floor plan file and save the SVG. Returns the SVG file path."""
    path = Path(image_path)
    if not path.exists():
        return None

    result = await convert_floor_plan(path.read_bytes())
    if not result:
        return None

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    svg_path = out_dir / "plan_svg.svg"
    svg_path.write_text(result["svg"], encoding="utf-8")

    json_path = out_dir / "plan_data.json"
    json_path.write_text(
        json.dumps(result["structured_data"], indent=2), encoding="utf-8"
    )

    logger.info(f"SVG saved: {svg_path} | JSON: {json_path}")
    return str(svg_path)


async def refine_svg(image_path: str, feedback: str) -> str | None:
    """Refine the SVG by reloading saved JSON, applying feedback, regenerating."""
    path = Path(image_path)
    if not path.exists():
        return None

    # Try to load the saved JSON; fall back to a fresh analysis
    json_path = path.parent / "plan_data.json"
    current_data = None
    if json_path.exists():
        try:
            current_data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            current_data = None

    if not current_data:
        # No saved JSON — re-run the first pass
        image_b64 = preprocess_floor_plan(path.read_bytes())
        current_data = await analyze_floor_plan(image_b64)
        if not current_data:
            return None

    result = await refine_floor_plan(path.read_bytes(), current_data, feedback)
    if not result:
        return None

    # Save updated files
    svg_path = path.parent / "plan_svg.svg"
    svg_path.write_text(result["svg"], encoding="utf-8")
    json_path.write_text(
        json.dumps(result["structured_data"], indent=2), encoding="utf-8"
    )

    return result["svg"]
