import os
import sys
import cv2
import numpy as np

# ============================================================
# CONFIG
# ============================================================

# Threshold for binarizing dark ink on light background
BIN_THRESH = 200

# Text / number removal
TEXT_MAX_HEIGHT = 28
TEXT_MAX_WIDTH = 80
TEXT_MAX_AREA = 900
TEXT_MAX_FILL = 0.55

# Wall extraction
WALL_H_KERNEL = 25
WALL_V_KERNEL = 25

# Outer shell sealing
OUTER_CLOSE_KERNEL = 121   # large enough to bridge big outer window gaps
OUTER_CONTOUR_THICKNESS = 6

# Door detection
DOOR_MIN_SIZE = 10
DOOR_MAX_SIZE = 140
DOOR_MAX_FILL = 0.35
DOOR_ARC_SCORE = 0.42
DOOR_ARC_ONLY_SCORE = 0.50
DOOR_LEAF_MIN = 6
DOOR_LOCAL_CLOSE = 3

# Bounding boxes
BOX_MIN_AREA = 10
BOX_COLOR = (0, 0, 255)
BOX_THICKNESS = 2


# ============================================================
# BASIC HELPERS
# ============================================================

def to_gray(img: np.ndarray) -> np.ndarray:
    if img is None:
        raise ValueError("Input image is None.")
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img.copy()


def binarize(gray: np.ndarray) -> np.ndarray:
    _, binary = cv2.threshold(gray, BIN_THRESH, 255, cv2.THRESH_BINARY_INV)
    return binary


def ensure_ext(path: str, default_ext: str = ".png") -> str:
    base = os.path.basename(path)
    if "." not in base:
        return path + default_ext
    return path


def safe_imwrite(path: str, image: np.ndarray) -> None:
    ok = cv2.imwrite(path, image)
    if not ok:
        raise RuntimeError(f"Failed to write image: {path}")


# ============================================================
# TEXT / NUMBER REMOVAL
# ============================================================

def component_mask_from_labels(labels: np.ndarray, idx: int) -> np.ndarray:
    mask = np.zeros(labels.shape, dtype=np.uint8)
    mask[labels == idx] = 255
    return mask


def is_textlike_component(x: int, y: int, w: int, h: int, area: int) -> bool:
    if area <= 0:
        return False

    fill = area / (w * h + 1e-6)

    # small symbols / letters / numbers
    if (
        h <= TEXT_MAX_HEIGHT
        and w <= TEXT_MAX_WIDTH
        and area <= TEXT_MAX_AREA
        and fill <= TEXT_MAX_FILL
    ):
        return True

    return False


def remove_text_and_numbers(binary: np.ndarray) -> np.ndarray:
    """
    Remove text/dimension labels while preserving walls and larger fixtures.
    Returns a new binary image (ink=255, background=0).
    """
    num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

    out = binary.copy()

    for i in range(1, num):
        x, y, w, h, area = stats[i]
        if is_textlike_component(x, y, w, h, area):
            out[labels == i] = 0

    return out


# ============================================================
# WALL EXTRACTION / OUTER SHELL SEALING
# ============================================================

def extract_walls(binary: np.ndarray) -> np.ndarray:
    """
    Extract long horizontal and vertical wall strokes.
    """
    horiz = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (WALL_H_KERNEL, 1)),
    )

    vert = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, WALL_V_KERNEL)),
    )

    walls = cv2.bitwise_or(horiz, vert)
    return walls


def seal_outer_walls(walls: np.ndarray) -> np.ndarray:
    """
    Seal large openings in the exterior shell by aggressively closing,
    then drawing the largest external contour back onto the wall mask.
    This does not identify windows; it just forces the outside boundary closed.
    """
    big_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (OUTER_CLOSE_KERNEL, OUTER_CLOSE_KERNEL)
    )

    closed = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, big_kernel)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    shell = np.zeros_like(walls)
    if contours:
        largest = max(contours, key=cv2.contourArea)
        cv2.drawContours(shell, [largest], -1, 255, thickness=OUTER_CONTOUR_THICKNESS)

    sealed = cv2.bitwise_or(walls, shell)
    return sealed


# ============================================================
# DOOR DETECTION
# ============================================================

def crop_component(mask: np.ndarray):
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None, None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    roi = mask[y0:y1 + 1, x0:x1 + 1].copy()
    return roi, (x0, y0, x1 + 1, y1 + 1)


def arc_band_score(
    mask: np.ndarray,
    cx: float,
    cy: float,
    r: float,
    start_deg: float,
    sweep: float = 90.0,
    band: int = 2,
    samples: int = 72,
) -> float:
    """
    Score whether there is ink near a quarter-circle arc.
    """
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

    if valid == 0:
        return 0.0
    return hits / valid


def best_arc_score(mask: np.ndarray) -> float:
    """
    Tolerant arc search. Tries candidate centers around the component bbox.
    """
    h, w = mask.shape
    if h < 3 or w < 3:
        return 0.0

    pts = np.column_stack(np.where(mask > 0))
    if len(pts) < 8:
        return 0.0

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
    for cx, cy in centers:
        for r in radii:
            for start in (0, 90, 180, 270):
                s = arc_band_score(mask, cx, cy, r, start)
                if s > best:
                    best = s

    return best


def has_leaf_line(mask: np.ndarray) -> bool:
    """
    Detect short line segment consistent with a remaining door leaf/stub.
    """
    lines = cv2.HoughLinesP(
        mask,
        1,
        np.pi / 180,
        threshold=6,
        minLineLength=DOOR_LEAF_MIN,
        maxLineGap=3,
    )
    return lines is not None


def looks_like_stairs(mask: np.ndarray) -> bool:
    """
    Reject staircase-like components: many repeated short lines.
    """
    lines = cv2.HoughLinesP(
        mask, 1, np.pi / 180, threshold=10, minLineLength=8, maxLineGap=2
    )
    return lines is not None and len(lines) >= 10


def looks_like_fixture(mask: np.ndarray) -> bool:
    """
    Reject larger bathroom / furniture fixtures.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False

    c = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(c)
    area = cv2.contourArea(c)
    fill = area / (w * h + 1e-6)

    if (w > 35 and h > 18 and area > 120) or fill > 0.45:
        return True

    return False


def is_door_component(component_mask: np.ndarray) -> bool:
    """
    Door classification on the tight component ROI.
    Supports:
      - leaf + arc
      - arc-only fallback for broken residual doors
    """
    roi, _ = crop_component(component_mask)
    if roi is None:
        return False

    h, w = roi.shape
    area = int(np.sum(roi > 0))
    fill = area / (h * w + 1e-6)
    max_dim = max(h, w)
    min_dim = min(h, w)

    if area < 8:
        return False
    if max_dim < DOOR_MIN_SIZE or max_dim > DOOR_MAX_SIZE:
        return False
    if fill > DOOR_MAX_FILL:
        return False

    arc_score = best_arc_score(roi)
    line_ok = has_leaf_line(roi)

    # Pass 1: normal door
    if arc_score >= DOOR_ARC_SCORE and line_ok:
        return True

    # Pass 2: broken arc-only door
    if arc_score >= DOOR_ARC_ONLY_SCORE:
        if looks_like_stairs(roi):
            return False
        if looks_like_fixture(roi):
            return False
        if fill < 0.22 and min_dim >= 6:
            return True

    return False


def remove_doors_from_residual(residual: np.ndarray):
    """
    Lightly reconnect broken door fragments, classify door components,
    and subtract them before boxing.
    """
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (DOOR_LOCAL_CLOSE, DOOR_LOCAL_CLOSE)
    )

    work = cv2.morphologyEx(
        residual,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1,
    )

    num, labels, stats, _ = cv2.connectedComponentsWithStats(work, connectivity=8)

    door_mask = np.zeros_like(residual)

    for i in range(1, num):
        x, y, w, h, area = stats[i]
        if area < 8:
            continue

        comp = np.zeros_like(residual)
        comp[labels == i] = 255

        if is_door_component(comp):
            door_mask[labels == i] = 255

    residual_no_doors = residual.copy()
    residual_no_doors[door_mask > 0] = 0

    return residual_no_doors, door_mask


# ============================================================
# BOXING / COLLISION
# ============================================================

def components_to_boxes(mask: np.ndarray):
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    boxes = []
    for i in range(1, num):
        x, y, w, h, area = stats[i]
        if area < BOX_MIN_AREA:
            continue
        boxes.append((int(x), int(y), int(w), int(h)))
    return boxes


def draw_boxes(gray: np.ndarray, boxes):
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for x, y, w, h in boxes:
        cv2.rectangle(vis, (x, y), (x + w, y + h), BOX_COLOR, BOX_THICKNESS)
    return vis


def build_collision_map(walls: np.ndarray, boxes):
    """
    Output: white background, black obstacles/walls.
    """
    collision = np.full(walls.shape, 255, dtype=np.uint8)

    # walls
    collision[walls > 0] = 0

    # object boxes as solid obstacles
    for x, y, w, h in boxes:
        cv2.rectangle(collision, (x, y), (x + w, y + h), 0, thickness=-1)

    return collision


# ============================================================
# MAIN PIPELINE
# ============================================================

def process(image: np.ndarray, debug: bool = False):
    gray = to_gray(image)
    binary = binarize(gray)

    # 1. remove text/numbers first so they never become boxes
    no_text = remove_text_and_numbers(binary)

    # 2. extract walls and seal outside shell
    walls = extract_walls(no_text)
    walls = seal_outer_walls(walls)

    # 3. residual = everything non-wall, non-text
    residual = cv2.subtract(no_text, walls)

    # 4. detect doors on residual and subtract them
    residual_no_doors, door_mask = remove_doors_from_residual(residual)

    # 5. everything remaining gets boxed
    boxes = components_to_boxes(residual_no_doors)

    # 6. outputs
    clean = np.full(gray.shape, 255, dtype=np.uint8)
    clean[walls > 0] = 0

    bounded = draw_boxes(gray, boxes)
    collision = build_collision_map(walls, boxes)

    debug_images = {
        "binary": binary,
        "no_text": no_text,
        "walls": walls,
        "residual": residual,
        "door_mask": door_mask,
        "residual_no_doors": residual_no_doors,
    }

    if debug:
        for name, img in debug_images.items():
            safe_imwrite(f"debug_{name}.png", img)

    return clean, bounded, collision


# ============================================================
# CLI
# ============================================================

def main():
    if len(sys.argv) < 5:
        print(
            "Usage: python fix_floorplan_cleanup_and_bound.py "
            "<input> <clean_out> <bounded_out> <collision_out> [--debug]"
        )
        sys.exit(1)

    inp = sys.argv[1]
    out_clean = ensure_ext(sys.argv[2])
    out_bound = ensure_ext(sys.argv[3])
    out_coll = ensure_ext(sys.argv[4])
    debug = "--debug" in sys.argv[5:]

    img = cv2.imread(inp)
    if img is None:
        raise FileNotFoundError(f"Could not read input image: {inp}")

    clean, bounded, collision = process(img, debug=debug)

    safe_imwrite(out_clean, clean)
    safe_imwrite(out_bound, bounded)
    safe_imwrite(out_coll, collision)

    print("done")
    print(f"clean:     {out_clean}")
    print(f"bounded:   {out_bound}")
    print(f"collision: {out_coll}")


if __name__ == "__main__":
    main()