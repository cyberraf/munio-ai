import cv2
import numpy as np

# ---------- config ----------
INPUT_PATH = "clean.png"
OUTPUT_PATH = "filled.png"

# Pick a point that is definitely inside the region you want to fill
SEED_POINT = (700, 500)   # (x, y) — change this if needed

# ---------- load ----------
img = cv2.imread(INPUT_PATH, cv2.IMREAD_GRAYSCALE)
if img is None:
    raise FileNotFoundError(f"Could not read: {INPUT_PATH}")

# ---------- threshold ----------
# Walls are dark, background is light
_, binary = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY)

# At this point:
# 255 = background/interior
# 0   = walls/lines

# ---------- close small gaps in walls ----------
# This helps prevent flood fill leakage through thin openings
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
closed = cv2.morphologyEx(255 - binary, cv2.MORPH_CLOSE, kernel, iterations=1)
# closed now has walls as white
closed = 255 - closed
# back to:
# 255 = open space
# 0   = walls

# ---------- flood fill ----------
flood = closed.copy()

h, w = flood.shape
mask = np.zeros((h + 2, w + 2), np.uint8)

# Fill the chosen region with gray value 128
cv2.floodFill(flood, mask, SEED_POINT, 128)

# Keep only the flood-filled area
filled_region = np.zeros_like(flood)
filled_region[flood == 128] = 255

# Optional: overlay result in color for visualization
overlay = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
overlay[filled_region == 255] = (0, 255, 0)

# ---------- save ----------
cv2.imwrite(OUTPUT_PATH, filled_region)
cv2.imwrite("filled_overlay.png", overlay)

print("Saved:")
print(" -", OUTPUT_PATH)
print(" - filled_overlay.png")