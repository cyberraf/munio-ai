"""
Robot Boundaries — fetches assigned rooms from the ASB backend and provides
point-in-polygon checks + boundary clamping for mock robot movement.

Usage:
    from robot_boundaries import RobotBoundaries

    bounds = RobotBoundaries.fetch("http://localhost:8080", "turtlebot-001")
    if bounds:
        x, y = bounds.clamp(x, y)           # push back inside if outside
        inside = bounds.contains(x, y)       # point-in-polygon check
        cx, cy = bounds.center()             # centroid of all assigned rooms
        min_x, min_y, max_x, max_y = bounds.bbox()  # axis-aligned bounding box
"""

import json
import math
import random
import urllib.request
from dataclasses import dataclass, field


@dataclass
class WorldRoom:
    id: str
    label: str
    polygon: list[tuple[float, float]]  # [(x, y), ...]


@dataclass
class RobotBoundaries:
    robot_id: str
    facility_id: str
    floor_id: str
    rooms: list[WorldRoom] = field(default_factory=list)

    @staticmethod
    def fetch(api_base: str, robot_id: str, robot_key: str = "asb-robot-key-2026") -> "RobotBoundaries | None":
        """Fetch boundaries from GET /api/robots/{id}/boundaries."""
        url = f"{api_base}/api/robots/{robot_id}/boundaries?key={robot_key}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"[boundaries] failed to fetch for {robot_id}: {e}")
            return None

        rooms = []
        for r in data.get("rooms", []):
            pts = [(p["x"], p["y"]) for p in r.get("polygon", [])]
            if len(pts) >= 3:
                rooms.append(WorldRoom(id=r["id"], label=r["label"], polygon=pts))

        if not rooms:
            print(f"[boundaries] no rooms returned for {robot_id}")
            return None

        b = RobotBoundaries(
            robot_id=robot_id,
            facility_id=data.get("facility_id", ""),
            floor_id=data.get("floor_id", ""),
            rooms=rooms,
        )
        print(f"[boundaries] {robot_id}: {len(rooms)} room(s) — bbox {b.bbox()}")
        return b

    def bbox(self) -> tuple[float, float, float, float]:
        """Return (min_x, min_y, max_x, max_y) bounding box of all rooms."""
        all_pts = [p for r in self.rooms for p in r.polygon]
        if not all_pts:
            return (0, 0, 5, 5)
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def center(self) -> tuple[float, float]:
        """Return centroid of all room polygons combined."""
        all_pts = [p for r in self.rooms for p in r.polygon]
        if not all_pts:
            return (2.5, 2.5)
        cx = sum(p[0] for p in all_pts) / len(all_pts)
        cy = sum(p[1] for p in all_pts) / len(all_pts)
        return (cx, cy)

    def random_point_inside(self) -> tuple[float, float]:
        """Return a random point inside one of the assigned rooms."""
        if not self.rooms:
            return self.center()
        room = random.choice(self.rooms)
        # Random point in polygon via rejection sampling within bbox
        xs = [p[0] for p in room.polygon]
        ys = [p[1] for p in room.polygon]
        for _ in range(200):
            x = random.uniform(min(xs), max(xs))
            y = random.uniform(min(ys), max(ys))
            if _point_in_polygon(x, y, room.polygon):
                return (x, y)
        # Fallback to centroid
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def contains(self, x: float, y: float) -> bool:
        """Check if (x, y) is inside any assigned room."""
        return any(_point_in_polygon(x, y, r.polygon) for r in self.rooms)

    def clamp(self, x: float, y: float) -> tuple[float, float]:
        """If (x, y) is outside all rooms, push it to the nearest room edge."""
        if self.contains(x, y):
            return (x, y)
        # Find closest point on any room polygon edge
        best_dist = float("inf")
        best_pt = (x, y)
        for room in self.rooms:
            pts = room.polygon
            for i in range(len(pts)):
                ax, ay = pts[i]
                bx, by = pts[(i + 1) % len(pts)]
                px, py = _closest_point_on_segment(x, y, ax, ay, bx, by)
                d = math.hypot(px - x, py - y)
                if d < best_dist:
                    best_dist = d
                    best_pt = (px, py)
        # Nudge slightly inside
        cx, cy = self.center()
        dx = cx - best_pt[0]
        dy = cy - best_pt[1]
        dist = math.hypot(dx, dy)
        if dist > 0.01:
            nudge = 0.05
            best_pt = (best_pt[0] + dx / dist * nudge, best_pt[1] + dy / dist * nudge)
        return best_pt


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting algorithm for point-in-polygon."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _closest_point_on_segment(px, py, ax, ay, bx, by):
    """Return the closest point on segment AB to point P."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (ax, ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return (ax + t * dx, ay + t * dy)
