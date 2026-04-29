"""
LIDAR-based 2D Mapper for ROS 2 robots (TurtleBot 4, etc.)

Builds an occupancy grid from 360° LIDAR scans + odometry.
Much higher quality than the PiCar-X ultrasonic mapper because each
scan gives hundreds of range readings covering the full circle.

Two modes:
  1. SLAM mode: subscribes to /map (OccupancyGrid from slam_toolbox/cartographer)
  2. Raw mode: builds the map from /scan + /odom directly
"""

import math
import time
import base64


class LidarMapper:
    """Builds a 2D occupancy grid from LIDAR scans + odometry (raw mode)."""

    def __init__(self, grid_size_m=10.0, resolution_cm=5):
        """
        Args:
            grid_size_m: map extent in meters (10m covers a large room)
            resolution_cm: grid cell size in cm
        """
        self.grid_size = grid_size_m
        self.resolution = resolution_cm / 100.0
        self.grid_dim = int(grid_size_m / self.resolution)

        # Occupancy grid: 0 = unknown, 1 = free, 2 = occupied
        self.grid = [[0] * self.grid_dim for _ in range(self.grid_dim)]

        # Robot trail
        self.trail = []  # [(x, y, timestamp_ms), ...]

        # Obstacle points (kept for the dashboard point cloud view)
        self.obstacle_points = []  # [(x, y), ...]

        # Timing
        self._last_send_ms = 0
        self._last_trail_ms = 0

    def update_from_scan(self, ranges, angle_min, angle_increment,
                         range_min, range_max,
                         robot_x, robot_y, robot_heading_rad,
                         timestamp_ms):
        """
        Process a LaserScan message to update the occupancy grid.

        Args:
            ranges: list of float distances
            angle_min: start angle (rad)
            angle_increment: angle step between readings (rad)
            range_min, range_max: valid range bounds (m)
            robot_x, robot_y: robot position from odometry (m)
            robot_heading_rad: robot heading from odometry (rad)
            timestamp_ms: epoch milliseconds
        """
        # Record trail
        if timestamp_ms - self._last_trail_ms > 500:
            self.trail.append((robot_x, robot_y, timestamp_ms))
            if len(self.trail) > 2000:
                self.trail.pop(0)
            self._last_trail_ms = timestamp_ms

        new_obstacles = []

        for i, distance in enumerate(ranges):
            # Skip invalid
            if distance < range_min or distance > range_max:
                continue
            if math.isinf(distance) or math.isnan(distance):
                continue

            # Beam angle in world frame
            beam_angle = angle_min + i * angle_increment
            world_angle = robot_heading_rad + beam_angle

            cos_a = math.cos(world_angle)
            sin_a = math.sin(world_angle)

            # Obstacle point in world coordinates
            obs_x = robot_x + distance * cos_a
            obs_y = robot_y + distance * sin_a

            new_obstacles.append((obs_x, obs_y))

            # Ray cast: mark free cells along the beam
            # Use larger step to avoid excessive computation on dense scans
            step = self.resolution
            d = step
            while d < distance:
                ray_x = robot_x + d * cos_a
                ray_y = robot_y + d * sin_a
                gx, gy = self._world_to_grid(ray_x, ray_y)
                if 0 <= gx < self.grid_dim and 0 <= gy < self.grid_dim:
                    self.grid[gy][gx] = 1  # free
                d += step

            # Mark obstacle cell
            gx, gy = self._world_to_grid(obs_x, obs_y)
            if 0 <= gx < self.grid_dim and 0 <= gy < self.grid_dim:
                self.grid[gy][gx] = 2  # occupied

        # Append obstacle points (with cap)
        self.obstacle_points.extend(new_obstacles)
        if len(self.obstacle_points) > 10000:
            self.obstacle_points = self.obstacle_points[-8000:]

    def update_from_occupancy_grid(self, data, width, height,
                                   resolution, origin_x, origin_y):
        """
        Process a nav_msgs/OccupancyGrid (from SLAM) directly.
        This gives the highest quality map.

        Args:
            data: flat list of int8 values (-1=unknown, 0=free, 100=occupied)
            width, height: grid dimensions
            resolution: meters per cell
            origin_x, origin_y: world position of cell (0,0)
        """
        # Map SLAM grid values to our format
        for y in range(min(height, self.grid_dim)):
            for x in range(min(width, self.grid_dim)):
                src_idx = y * width + x
                if src_idx >= len(data):
                    break

                val = data[src_idx]
                # Convert world to our grid
                wx = origin_x + x * resolution
                wy = origin_y + y * resolution
                gx, gy = self._world_to_grid(wx, wy)

                if 0 <= gx < self.grid_dim and 0 <= gy < self.grid_dim:
                    if val == -1:
                        pass  # unknown, leave as 0
                    elif val < 50:
                        self.grid[gy][gx] = 1  # free
                    else:
                        self.grid[gy][gx] = 2  # occupied
                        self.obstacle_points.append((wx, wy))

        if len(self.obstacle_points) > 10000:
            self.obstacle_points = self.obstacle_points[-8000:]

    def should_send(self, timestamp_ms, interval_ms=3000):
        """Returns True if enough time has passed for a map update."""
        if timestamp_ms - self._last_send_ms >= interval_ms:
            self._last_send_ms = timestamp_ms
            return True
        return False

    def get_map_data(self, robot_id, robot_x, robot_y, robot_heading_rad):
        """Returns the map state for the WebSocket."""
        return {
            "type": "map_update",
            "robot_id": robot_id,
            "grid_dim": self.grid_dim,
            "resolution_cm": int(self.resolution * 100),
            "grid_size_m": self.grid_size,
            "robot_x": round(robot_x, 3),
            "robot_y": round(robot_y, 3),
            "robot_heading": round(robot_heading_rad, 3),
            "trail": [
                (round(x, 2), round(y, 2))
                for x, y, _ in self.trail[-500:]
            ],
            "obstacle_points": [
                (round(x, 2), round(y, 2))
                for x, y in self.obstacle_points[-3000:]
            ],
            "grid_rle": self._rle_encode_grid(),
        }

    def _world_to_grid(self, wx, wy):
        """Convert world meters to grid indices (centered)."""
        gx = int((wx + self.grid_size / 2) / self.resolution)
        gy = int((wy + self.grid_size / 2) / self.resolution)
        return gx, gy

    def _rle_encode_grid(self):
        """Run-length encode the occupancy grid."""
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


class ROS2CameraCapture:
    """Captures snapshots from a ROS 2 camera topic."""

    def __init__(self):
        self.available = False
        self.latest_frame = None
        self._lock = __import__("threading").Lock()
        self._last_snapshot_ms = 0

    def start(self, node):
        """Subscribe to a camera topic on the given ROS 2 node."""
        try:
            from sensor_msgs.msg import Image
            # Try common camera topics
            for topic in [
                "/oakd/rgb/preview/image_raw",
                "/camera/image_raw",
                "/image_raw",
            ]:
                node.create_subscription(Image, topic, self._image_cb, 1)
                node.get_logger().info(f"Subscribed to camera: {topic}")
                self.available = True
                break
        except Exception as e:
            print(f"[camera] not available: {e}")

    def _image_cb(self, msg):
        """Store raw image data from ROS 2 Image message."""
        with self._lock:
            self.latest_frame = msg

    def should_snapshot(self, timestamp_ms, interval_ms=10000):
        if not self.available or self.latest_frame is None:
            return False
        if timestamp_ms - self._last_snapshot_ms >= interval_ms:
            self._last_snapshot_ms = timestamp_ms
            return True
        return False

    def capture_snapshot(self, robot_id, timestamp_ms):
        """Convert latest ROS 2 Image to a base64 JPEG snapshot message."""
        with self._lock:
            if self.latest_frame is None:
                return None
            frame = self.latest_frame

        try:
            import cv2
            import numpy as np

            # Convert ROS Image to numpy array
            if frame.encoding in ("rgb8", "bgr8"):
                img = np.frombuffer(frame.data, dtype=np.uint8).reshape(
                    frame.height, frame.width, 3
                )
                if frame.encoding == "rgb8":
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                # Fallback: try raw interpretation
                img = np.frombuffer(frame.data, dtype=np.uint8).reshape(
                    frame.height, frame.width, -1
                )

            # Resize for bandwidth
            h, w = img.shape[:2]
            if w > 320:
                scale = 320 / w
                img = cv2.resize(img, (320, int(h * scale)))

            _, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
            b64 = base64.b64encode(jpeg.tobytes()).decode("ascii")

            return {
                "type": "camera_snapshot",
                "robot_id": robot_id,
                "image": b64,
                "edge_count": 0,
                "timestamp_ms": timestamp_ms,
            }
        except Exception as e:
            print(f"[camera] snapshot error: {e}")
            return None
