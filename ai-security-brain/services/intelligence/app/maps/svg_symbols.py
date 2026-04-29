"""SVG symbol rendering library for floor plan elements.

Each function returns an SVG fragment styled to match the ASB dark theme.
The master `render_element_to_svg` routes a typed element dict to the
correct renderer.
"""

import math


# ═══════════════════════════════════════════════════
# DOORS
# ═══════════════════════════════════════════════════


def svg_door_swing(hinge_x, hinge_y, swing_end_x, swing_end_y, radius,
                   start_angle, end_angle, _door_type="single_swing"):
    """Render a swing door with panel line and arc."""
    # Door panel (straight line from hinge to end of swing)
    panel = (f'<line x1="{hinge_x}" y1="{hinge_y}" '
             f'x2="{swing_end_x}" y2="{swing_end_y}" '
             f'stroke="#8899AA" stroke-width="2" fill="none"/>')

    # Swing arc using SVG arc path command
    sa = math.radians(start_angle)
    ea = math.radians(end_angle)
    arc_end_x = hinge_x + radius * math.cos(ea)
    arc_end_y = hinge_y + radius * math.sin(ea)
    arc_start_x = hinge_x + radius * math.cos(sa)
    arc_start_y = hinge_y + radius * math.sin(sa)

    large_arc = 1 if abs(end_angle - start_angle) > 180 else 0
    sweep = 1 if end_angle > start_angle else 0

    arc = (f'<path d="M {arc_start_x},{arc_start_y} '
           f'A {radius},{radius} 0 {large_arc},{sweep} {arc_end_x},{arc_end_y}" '
           f'stroke="#8899AA" stroke-width="1" stroke-dasharray="3,2" fill="none" opacity="0.6"/>')

    return f'<g class="door door-swing" data-symbol="door">{panel}{arc}</g>'


def svg_door_sliding(x, y, width, _height, orientation=0):
    """Render a sliding door (two overlapping panels)."""
    half = width / 2
    return (f'<g class="door door-sliding" data-symbol="door" '
            f'transform="rotate({orientation},{x},{y})">'
            f'<rect x="{x - half}" y="{y - 2}" width="{half + 5}" height="4" '
            f'fill="#8899AA" opacity="0.7"/>'
            f'<rect x="{x - 5}" y="{y - 2}" width="{half + 5}" height="4" '
            f'fill="#64748B" opacity="0.5"/>'
            f'<line x1="{x - half}" y1="{y}" x2="{x + half}" y2="{y}" '
            f'stroke="#4B5563" stroke-width="1"/>'
            f'</g>')


def svg_door_bifold(x, y, width, _height, orientation=0):
    """Render a bifold door (zigzag pattern)."""
    segments = 4
    seg_w = width / segments
    points = []
    for i in range(segments + 1):
        px = x - width / 2 + i * seg_w
        py = y + (5 if i % 2 == 0 else -5)
        points.append(f"{px},{py}")
    polyline = " ".join(points)

    return (f'<g class="door door-bifold" data-symbol="door" '
            f'transform="rotate({orientation},{x},{y})">'
            f'<polyline points="{polyline}" '
            f'stroke="#8899AA" stroke-width="2" fill="none"/>'
            f'</g>')


# ═══════════════════════════════════════════════════
# WINDOWS
# ═══════════════════════════════════════════════════


def svg_window(start_x, start_y, end_x, end_y, wall_thickness=8):
    """Render a window as parallel lines within the wall."""
    dx = end_x - start_x
    dy = end_y - start_y
    length = math.sqrt(dx * dx + dy * dy)
    if length == 0:
        return ""

    # Perpendicular normal
    nx = -dy / length * (wall_thickness / 3)
    ny = dx / length * (wall_thickness / 3)

    lines = []
    for offset in [-1, 0, 1]:
        ox = nx * offset
        oy = ny * offset
        lines.append(
            f'<line x1="{start_x + ox}" y1="{start_y + oy}" '
            f'x2="{end_x + ox}" y2="{end_y + oy}" '
            f'stroke="#5BA4E6" stroke-width="1" opacity="0.7"/>'
        )

    return f'<g class="window" data-symbol="window">{"".join(lines)}</g>'


# ═══════════════════════════════════════════════════
# BATHROOM FIXTURES
# ═══════════════════════════════════════════════════


def svg_toilet(x, y, width, height, orientation=0):
    """Render a toilet (tank rectangle + bowl oval)."""
    tank_h = height * 0.3
    bowl_h = height * 0.7

    return (f'<g class="fixture toilet" data-symbol="toilet" '
            f'transform="rotate({orientation},{x},{y})">'
            f'<rect x="{x - width/2}" y="{y - height/2}" '
            f'width="{width}" height="{tank_h}" '
            f'fill="#2A3A4E" stroke="#4B5563" stroke-width="1" rx="2"/>'
            f'<ellipse cx="{x}" cy="{y - height/2 + tank_h + bowl_h/2}" '
            f'rx="{width/2 - 2}" ry="{bowl_h/2 - 2}" '
            f'fill="#1E293B" stroke="#4B5563" stroke-width="1"/>'
            f'</g>')


def svg_bathtub(x, y, width, height, orientation=0):
    """Render a bathtub (rectangle with X pattern and rounded corners)."""
    return (f'<g class="fixture bathtub" data-symbol="bathtub" '
            f'transform="rotate({orientation},{x},{y})">'
            f'<rect x="{x - width/2}" y="{y - height/2}" '
            f'width="{width}" height="{height}" '
            f'fill="#1E293B" stroke="#4B5563" stroke-width="1.5" rx="4"/>'
            f'<rect x="{x - width/2 + 4}" y="{y - height/2 + 4}" '
            f'width="{width - 8}" height="{height - 8}" '
            f'fill="none" stroke="#374151" stroke-width="0.5" rx="3"/>'
            f'<line x1="{x - width/2 + 6}" y1="{y - height/2 + 6}" '
            f'x2="{x + width/2 - 6}" y2="{y + height/2 - 6}" '
            f'stroke="#374151" stroke-width="0.5" opacity="0.4"/>'
            f'<line x1="{x + width/2 - 6}" y1="{y - height/2 + 6}" '
            f'x2="{x - width/2 + 6}" y2="{y + height/2 - 6}" '
            f'stroke="#374151" stroke-width="0.5" opacity="0.4"/>'
            f'<circle cx="{x}" cy="{y + height/4}" r="3" '
            f'fill="none" stroke="#4B5563" stroke-width="0.5"/>'
            f'</g>')


def svg_shower(x, y, width, height, orientation=0):
    """Render a shower (square with drain circle and tile pattern)."""
    return (f'<g class="fixture shower" data-symbol="shower" '
            f'transform="rotate({orientation},{x},{y})">'
            f'<rect x="{x - width/2}" y="{y - height/2}" '
            f'width="{width}" height="{height}" '
            f'fill="#1A2332" stroke="#4B5563" stroke-width="1" rx="2"/>'
            f'<line x1="{x}" y1="{y - height/2}" x2="{x}" y2="{y + height/2}" '
            f'stroke="#2A3A4E" stroke-width="0.3"/>'
            f'<line x1="{x - width/2}" y1="{y}" x2="{x + width/2}" y2="{y}" '
            f'stroke="#2A3A4E" stroke-width="0.3"/>'
            f'<circle cx="{x}" cy="{y}" r="3" fill="none" stroke="#4B5563" stroke-width="0.5"/>'
            f'<circle cx="{x}" cy="{y - height/2 + 8}" r="4" '
            f'fill="none" stroke="#5BA4E6" stroke-width="0.5" opacity="0.5"/>'
            f'</g>')


def svg_sink(x, y, width, height, _fixture_type="bathroom", basin_count=1, orientation=0):
    """Render a sink (counter rectangle + basin oval(s))."""
    elements = [
        f'<rect x="{x - width/2}" y="{y - height/2}" '
        f'width="{width}" height="{height}" '
        f'fill="#2A3A4E" stroke="#4B5563" stroke-width="1" rx="2"/>'
    ]

    if basin_count == 1:
        elements.append(
            f'<ellipse cx="{x}" cy="{y}" '
            f'rx="{width/2 - 4}" ry="{height/2 - 4}" '
            f'fill="#1A2332" stroke="#4B5563" stroke-width="0.5"/>'
        )
    elif basin_count >= 2:
        offset = width / 4
        for dx in [-offset, offset]:
            elements.append(
                f'<ellipse cx="{x + dx}" cy="{y}" '
                f'rx="{width/4 - 3}" ry="{height/2 - 4}" '
                f'fill="#1A2332" stroke="#4B5563" stroke-width="0.5"/>'
            )

    return (f'<g class="fixture sink" data-symbol="sink" '
            f'transform="rotate({orientation},{x},{y})">'
            f'{"".join(elements)}</g>')


# ═══════════════════════════════════════════════════
# KITCHEN FIXTURES
# ═══════════════════════════════════════════════════


def svg_cooktop(x, y, width, height, burner_count=4, orientation=0):
    """Render a cooktop with burner circles."""
    elements = [
        f'<rect x="{x - width/2}" y="{y - height/2}" '
        f'width="{width}" height="{height}" '
        f'fill="#2A3A4E" stroke="#4B5563" stroke-width="1" rx="2"/>'
    ]

    if burner_count == 4:
        positions = [(-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25)]
    elif burner_count == 5:
        positions = [(-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25), (0, 0)]
    else:
        positions = [(0, 0)]

    r_large = min(width, height) * 0.15
    r_small = r_large * 0.5

    for px, py in positions:
        cx = x + px * width * 0.7
        cy = y + py * height * 0.7
        elements.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r_large}" '
            f'fill="none" stroke="#64748B" stroke-width="1"/>'
        )
        elements.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r_small}" '
            f'fill="none" stroke="#64748B" stroke-width="0.5"/>'
        )

    return (f'<g class="fixture cooktop" data-symbol="cooktop" '
            f'transform="rotate({orientation},{x},{y})">'
            f'{"".join(elements)}</g>')


def svg_refrigerator(x, y, width, height, orientation=0):
    """Render a refrigerator."""
    return (f'<g class="fixture refrigerator" data-symbol="refrigerator" '
            f'transform="rotate({orientation},{x},{y})">'
            f'<rect x="{x - width/2}" y="{y - height/2}" '
            f'width="{width}" height="{height}" '
            f'fill="#2A3A4E" stroke="#4B5563" stroke-width="1.5" rx="2"/>'
            f'<line x1="{x + width/2 - 5}" y1="{y - height/4}" '
            f'x2="{x + width/2 - 5}" y2="{y + height/4}" '
            f'stroke="#64748B" stroke-width="1.5" stroke-linecap="round"/>'
            f'<line x1="{x - width/2}" y1="{y - 2}" '
            f'x2="{x + width/2}" y2="{y - 2}" '
            f'stroke="#374151" stroke-width="0.5"/>'
            f'</g>')


# ═══════════════════════════════════════════════════
# LAUNDRY
# ═══════════════════════════════════════════════════


def svg_washer_dryer(x, y, width, height, _stacked=False, label="W/D", orientation=0):
    """Render a washer or dryer (square with circle)."""
    return (f'<g class="fixture washer-dryer" data-symbol="washer_dryer" '
            f'transform="rotate({orientation},{x},{y})">'
            f'<rect x="{x - width/2}" y="{y - height/2}" '
            f'width="{width}" height="{height}" '
            f'fill="#2A3A4E" stroke="#4B5563" stroke-width="1" rx="2"/>'
            f'<circle cx="{x}" cy="{y}" r="{min(width,height)/2 - 6}" '
            f'fill="#1A2332" stroke="#4B5563" stroke-width="0.5"/>'
            f'<text x="{x}" y="{y + 3}" text-anchor="middle" '
            f'fill="#64748B" font-size="8" font-family="Inter">{label}</text>'
            f'</g>')


# ═══════════════════════════════════════════════════
# CLOSET
# ═══════════════════════════════════════════════════


def svg_closet_rod(x, y, width, orientation=0):
    """Render a closet rod with hanger indicators."""
    elements = [
        f'<line x1="{x - width/2}" y1="{y}" '
        f'x2="{x + width/2}" y2="{y}" '
        f'stroke="#4B5563" stroke-width="1.5"/>'
    ]
    hanger_count = max(1, int(width / 8))
    for i in range(hanger_count):
        hx = x - width / 2 + (i + 0.5) * (width / hanger_count)
        elements.append(
            f'<path d="M {hx-3},{y+1} L {hx},{y+7} L {hx+3},{y+1}" '
            f'stroke="#374151" stroke-width="0.5" fill="none"/>'
        )

    return (f'<g class="closet-rod" data-symbol="closet_rod" '
            f'transform="rotate({orientation},{x},{y})">'
            f'{"".join(elements)}</g>')


# ═══════════════════════════════════════════════════
# STAIRS
# ═══════════════════════════════════════════════════


def svg_staircase(x, y, width, height, tread_count=10, direction="up", orientation=0):
    """Render a staircase with treads and direction arrow."""
    elements = [
        f'<rect x="{x - width/2}" y="{y - height/2}" '
        f'width="{width}" height="{height}" '
        f'fill="#1A2332" stroke="#4B5563" stroke-width="1"/>'
    ]
    tread_h = height / tread_count
    for i in range(1, tread_count):
        ty = y - height / 2 + i * tread_h
        elements.append(
            f'<line x1="{x - width/2}" y1="{ty}" '
            f'x2="{x + width/2}" y2="{ty}" '
            f'stroke="#374151" stroke-width="0.5"/>'
        )
    arrow_y = y - height / 4 if direction == "up" else y + height / 4
    elements.append(
        f'<text x="{x}" y="{arrow_y}" text-anchor="middle" '
        f'fill="#64748B" font-size="7" font-family="Inter">'
        f'{"▲ UP" if direction == "up" else "▼ DN"}</text>'
    )

    return (f'<g class="staircase" data-symbol="staircase" '
            f'transform="rotate({orientation},{x},{y})">'
            f'{"".join(elements)}</g>')


# ═══════════════════════════════════════════════════
# MASTER RENDERER
# ═══════════════════════════════════════════════════


def render_element_to_svg(element_type: str, element: dict) -> str:
    """Route an element to the correct SVG renderer."""

    def _door_swing(e):
        return svg_door_swing(
            e["hinge_x"], e["hinge_y"], e["swing_end_x"], e["swing_end_y"],
            e["swing_radius"],
            e.get("swing_start_angle", 0), e.get("swing_end_angle", 90),
        )

    renderers = {
        # Doors
        "single_swing": _door_swing,
        "double_swing": _door_swing,
        "swing": _door_swing,
        "single": _door_swing,
        "double": _door_swing,
        "sliding": lambda e: svg_door_sliding(e["x"], e["y"], e["width"], e.get("height", 8), e.get("orientation", 0)),
        "bifold": lambda e: svg_door_bifold(e["x"], e["y"], e["width"], e.get("height", 8), e.get("orientation", 0)),

        # Windows
        "standard_window": lambda e: svg_window(e["start_x"], e["start_y"], e["end_x"], e["end_y"]),
        "sliding_window": lambda e: svg_window(e["start_x"], e["start_y"], e["end_x"], e["end_y"]),
        "picture_window": lambda e: svg_window(e["start_x"], e["start_y"], e["end_x"], e["end_y"]),

        # Bathroom
        "toilet": lambda e: svg_toilet(e["x"], e["y"], e["width"], e["height"], e.get("orientation", 0)),
        "bathtub": lambda e: svg_bathtub(e["x"], e["y"], e["width"], e["height"], e.get("orientation", 0)),
        "shower": lambda e: svg_shower(e["x"], e["y"], e["width"], e["height"], e.get("orientation", 0)),

        # Sinks (bathroom and kitchen) — fixture_type is positional/ignored by renderer
        "sink": lambda e: svg_sink(
            e["x"], e["y"], e["width"], e["height"],
            e.get("fixture_type", "bathroom"),
            basin_count=e.get("basin_count", 1),
            orientation=e.get("orientation", 0),
        ),
        "kitchen_sink": lambda e: svg_sink(
            e["x"], e["y"], e["width"], e["height"], "kitchen",
            basin_count=e.get("basin_count", 1),
            orientation=e.get("orientation", 0),
        ),

        # Kitchen
        "cooktop": lambda e: svg_cooktop(e["x"], e["y"], e["width"], e["height"],
                                         e.get("burner_count", 4), e.get("orientation", 0)),
        "stove": lambda e: svg_cooktop(e["x"], e["y"], e["width"], e["height"],
                                       e.get("burner_count", 4), e.get("orientation", 0)),
        "refrigerator": lambda e: svg_refrigerator(e["x"], e["y"], e["width"], e["height"], e.get("orientation", 0)),

        # Laundry
        "washer_dryer_stacked": lambda e: svg_washer_dryer(
            e["x"], e["y"], e["width"], e["height"],
            label=e.get("label", "W/D"), orientation=e.get("orientation", 0),
        ),
        "washer_dryer": lambda e: svg_washer_dryer(
            e["x"], e["y"], e["width"], e["height"],
            label=e.get("label", "W/D"), orientation=e.get("orientation", 0),
        ),
        "washer": lambda e: svg_washer_dryer(
            e["x"], e["y"], e["width"], e["height"],
            label="W", orientation=e.get("orientation", 0),
        ),
        "dryer": lambda e: svg_washer_dryer(
            e["x"], e["y"], e["width"], e["height"],
            label="D", orientation=e.get("orientation", 0),
        ),

        # Closet
        "closet_rod": lambda e: svg_closet_rod(
            e.get("x", 0), e.get("y", 0),
            e.get("width", 60), e.get("orientation", 0)
        ),

        # Stairs
        "staircase": lambda e: svg_staircase(e["x"], e["y"], e["width"], e["height"],
                                             e.get("tread_count", 10), e.get("direction", "up"),
                                             e.get("orientation", 0)),
    }

    renderer = renderers.get(element_type)
    if renderer:
        try:
            return renderer(element)
        except (KeyError, TypeError) as err:
            return f'<!-- Failed to render {element_type}: {err} -->'
    return f'<!-- Unknown element type: {element_type} -->'
