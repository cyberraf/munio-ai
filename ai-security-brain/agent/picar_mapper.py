"""
PiCar-X 2D Mapper
Builds an occupancy grid from ultrasonic distance readings + dead reckoning.

The PiCar-X has a single forward-facing ultrasonic sensor. As the robot
drives and turns, each distance reading becomes a point in 2D space:
    obstacle_x = robot_x + distance * cos(heading)
    obstacle_y = robot_y + distance * sin(heading)

Over time these points accumulate into a map of obstacles.
"""

import math
import time
import base64


class PicarMapper:
    """Builds a 2D occupancy grid from dead-reckoned position + ultrasonic."""

    def __init__(self, grid_size_m=5.0, resolution_cm=5, boundaries=None):
        """
        Args:
            grid_size_m: total map size in meters (5m covers a room)
            resolution_cm: each grid cell in cm (5cm = 100x100 grid for 5m)
            boundaries: optional RobotBoundaries instance for constraining movement
        """
        self.grid_size = grid_size_m
        self.resolution = resolution_cm / 100.0
        self.grid_dim = int(grid_size_m / self.resolution)
        self.bounds = boundaries

        # Occupancy grid: 0 = unknown, 1 = free, 2 = occupied
        self.grid = [[0] * self.grid_dim for _ in range(self.grid_dim)]

        # Robot state (dead reckoning)
        if self.bounds:
            self.x, self.y = self.bounds.random_point_inside()
        else:
            self.x = grid_size_m / 2  # start at center
            self.y = grid_size_m / 2
        self.heading = 0.0  # radians, 0 = facing right
        self.last_update_time = None

        # Trail of robot positions
        self.trail = []  # [(x, y, timestamp_ms), ...]

        # Obstacle points
        self.obstacle_points = []  # [(x, y, timestamp_ms), ...]

        # Timestamp of last map_data send
        self._last_send_ms = 0

    def update(self, speed, steering_angle_deg, distance_cm, timestamp_ms):
        """
        Called on every telemetry event (~10 Hz).

        Args:
            speed: 0-100 motor power. Roughly speed * 0.003 m/s.
            steering_angle_deg: -40 to +40 degrees.
            distance_cm: ultrasonic reading. -1 = sensor error.
            timestamp_ms: epoch milliseconds.
        """
        current_time = timestamp_ms / 1000.0
        if self.last_update_time is None:
            self.last_update_time = current_time
            return

        dt = current_time - self.last_update_time
        self.last_update_time = current_time

        # Skip bad timing
        if dt <= 0 or dt > 1.0:
            return

        # Convert speed to m/s (PiCar-X calibration: speed=40 ~ 0.12 m/s)
        speed_ms = abs(speed) * 0.003

        # Update heading from steering angle.
        # At full steering (40 deg) + cruise speed the robot turns ~45 deg/s.
        steering_rad = math.radians(steering_angle_deg)
        turn_rate = steering_rad * speed_ms * 2.0
        self.heading += turn_rate * dt

        # Update position
        self.x += speed_ms * math.cos(self.heading) * dt
        self.y += speed_ms * math.sin(self.heading) * dt

        # Clamp to assigned room boundaries
        if self.bounds:
            self.x, self.y = self.bounds.clamp(self.x, self.y)

        # Record trail (~every 500 ms)
        if not self.trail or (timestamp_ms - self.trail[-1][2]) > 500:
            self.trail.append((self.x, self.y, timestamp_ms))
            if len(self.trail) > 1000:
                self.trail.pop(0)

        # Ray cast + obstacle detection
        if 0 < distance_cm < 400:
            distance_m = distance_cm / 100.0

            # Obstacle point in world coordinates
            obs_x = self.x + distance_m * math.cos(self.heading)
            obs_y = self.y + distance_m * math.sin(self.heading)

            self.obstacle_points.append((obs_x, obs_y, timestamp_ms))
            if len(self.obstacle_points) > 5000:
                self.obstacle_points.pop(0)

            # Mark cells along the ray as free
            steps = int(distance_m / self.resolution)
            for i in range(steps):
                frac = i / max(steps, 1)
                ray_x = self.x + frac * distance_m * math.cos(self.heading)
                ray_y = self.y + frac * distance_m * math.sin(self.heading)
                gx, gy = self._world_to_grid(ray_x, ray_y)
                if 0 <= gx < self.grid_dim and 0 <= gy < self.grid_dim:
                    self.grid[gy][gx] = 1  # free

            # Mark obstacle cell as occupied
            gx, gy = self._world_to_grid(obs_x, obs_y)
            if 0 <= gx < self.grid_dim and 0 <= gy < self.grid_dim:
                self.grid[gy][gx] = 2  # occupied

    def should_send(self, timestamp_ms, interval_ms=2000):
        """Returns True if enough time has passed to send a map update."""
        if timestamp_ms - self._last_send_ms >= interval_ms:
            self._last_send_ms = timestamp_ms
            return True
        return False

    def get_map_data(self):
        """Returns the current map state for sending to the backend."""
        return {
            "type": "map_update",
            "grid_dim": self.grid_dim,
            "resolution_cm": int(self.resolution * 100),
            "grid_size_m": self.grid_size,
            "robot_x": round(self.x, 3),
            "robot_y": round(self.y, 3),
            "robot_heading": round(self.heading, 3),
            "trail": [(round(x, 2), round(y, 2)) for x, y, _ in self.trail[-200:]],
            "obstacle_points": [
                (round(x, 2), round(y, 2)) for x, y, _ in self.obstacle_points[-2000:]
            ],
            "grid_rle": self._rle_encode_grid(),
        }

    def _world_to_grid(self, wx, wy):
        """Convert world coordinates (meters) to grid indices."""
        return int(wx / self.resolution), int(wy / self.resolution)

    def _rle_encode_grid(self):
        """Run-length encode the occupancy grid for efficient transport."""
        flat = []
        for row in self.grid:
            flat.extend(row)

        if not flat:
            return []

        rle = []
        current = flat[0]
        count = 1
        for val in flat[1:]:
            if val == current:
                count += 1
            else:
                rle.append([current, count])
                current = val
                count = 1
        rle.append([current, count])
        return rle

    def reset(self):
        """Clear the map."""
        self.grid = [[0] * self.grid_dim for _ in range(self.grid_dim)]
        self.trail.clear()
        self.obstacle_points.clear()
        self.x = self.grid_size / 2
        self.y = self.grid_size / 2
        self.heading = 0.0
        self.last_update_time = None


class CameraMapper:
    """
    Optional camera integration for the PiCar-X.
    Captures low-res JPEG snapshots and runs simple edge detection.
    """

    def __init__(self):
        self.available = False
        self.camera = None
        self.cv2 = None
        self._last_snapshot_ms = 0
        try:
            from picamera2 import Picamera2
            import cv2

            self.camera = Picamera2()
            config = self.camera.create_still_configuration(
                main={"size": (320, 240)},
            )
            self.camera.configure(config)
            self.camera.start()
            self.cv2 = cv2
            self.available = True
            print("[mapper] camera initialized (320x240)")
        except Exception as e:
            print(f"[mapper] camera not available: {e}")

    def should_snapshot(self, timestamp_ms, interval_ms=10000):
        """Returns True if enough time has passed for a snapshot."""
        if not self.available:
            return False
        if timestamp_ms - self._last_snapshot_ms >= interval_ms:
            self._last_snapshot_ms = timestamp_ms
            return True
        return False

    def capture_snapshot(self):
        """Capture a low-res frame, return as base64 JPEG string."""
        if not self.available:
            return None
        try:
            frame = self.camera.capture_array()
            _, jpeg = self.cv2.imencode(
                ".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 50]
            )
            return base64.b64encode(jpeg.tobytes()).decode("ascii")
        except Exception:
            return None

    def detect_edges_ahead(self):
        """
        Simple edge detection to estimate obstacle complexity.
        Returns edge pixel count in the center strip of the frame.
        High = complex obstacle, low = open space.
        """
        if not self.available:
            return 0
        try:
            frame = self.camera.capture_array()
            gray = self.cv2.cvtColor(frame, self.cv2.COLOR_RGB2GRAY)
            edges = self.cv2.Canny(gray, 50, 150)
            h, w = edges.shape
            center_strip = edges[h // 3 : 2 * h // 3, w // 4 : 3 * w // 4]
            return int(center_strip.sum() / 255)
        except Exception:
            return 0

    def get_snapshot_message(self, robot_id, timestamp_ms):
        """Build a camera snapshot message for the WebSocket."""
        image_b64 = self.capture_snapshot()
        if image_b64 is None:
            return None
        return {
            "type": "camera_snapshot",
            "robot_id": robot_id,
            "image": image_b64,
            "edge_count": self.detect_edges_ahead(),
            "timestamp_ms": timestamp_ms,
        }
