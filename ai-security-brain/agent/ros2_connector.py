"""
ROS 2 Fleet Connector — TurtleBot Edition
Subscribes to standard ROS 2 topics, normalizes telemetry into ASB schema,
and streams to the AI Security Brain backend via WebSocket at configurable Hz.

Usage:
  With ROS 2:  python3 ros2_connector.py
  Mock mode:   python3 ros2_connector.py --mock

Requires: ROS 2 Humble+ (for real mode), websockets, numpy
"""

import argparse
import asyncio
import json
import math
import random
import signal
import sys
import threading
import time

import websockets

# ─── Configuration ───────────────────────────────────────────────────────────

BACKEND_WS = "ws://YOUR_BACKEND_IP:8080/ws/telemetry?key=asb-robot-key-2026"  # edit this
ROBOT_ID = "turtlebot-001"
ROBOT_TYPE = "turtlebot4"
VENDOR = "ros2"
FACILITY_ID = "lab-001"
SAMPLE_RATE_HZ = 10
MAX_SPEED_MS = 1.0  # TurtleBot4 max speed ~0.46 m/s; scale 0–1 m/s → 0–100
RECONNECT_BASE = 2
RECONNECT_MAX = 10
FORWARD_ARC_DEG = 30  # ±30° for minimum distance extraction from LIDAR


# ─── Shared telemetry state (thread-safe) ────────────────────────────────────

class TelemetryState:
    """Thread-safe container for the latest sensor readings."""

    def __init__(self, robot_id, robot_type, vendor, facility_id):
        self._lock = threading.Lock()
        self.robot_id = robot_id
        self.robot_type = robot_type
        self.vendor = vendor
        self.facility_id = facility_id
        self.position_x = 0.0
        self.position_y = 0.0
        self.heading_deg = 0.0
        self.heading_rad = 0.0
        self.speed_ms = 0.0
        self.distance_cm = 999.0  # 999 = no LIDAR data
        self.battery_voltage = 12.6
        self.battery_pct = 100.0
        self.status = "active"
        # Raw scan data for the LIDAR mapper
        self.latest_scan = None  # dict with ranges, angle_min, etc.

    def update_odom(self, x, y, heading_deg, speed_ms):
        with self._lock:
            self.position_x = x
            self.position_y = y
            self.heading_deg = heading_deg
            self.heading_rad = math.radians(heading_deg)
            self.speed_ms = speed_ms

    def update_scan(self, min_distance_cm):
        with self._lock:
            self.distance_cm = min_distance_cm

    def update_scan_full(self, ranges, angle_min, angle_increment, range_min, range_max):
        """Store the full scan for the LIDAR mapper."""
        with self._lock:
            self.latest_scan = {
                "ranges": list(ranges),
                "angle_min": angle_min,
                "angle_increment": angle_increment,
                "range_min": range_min,
                "range_max": range_max,
            }

    def pop_scan(self):
        """Retrieve and clear the latest scan data."""
        with self._lock:
            scan = self.latest_scan
            self.latest_scan = None
            return scan

    def update_battery(self, voltage, pct):
        with self._lock:
            self.battery_voltage = voltage
            self.battery_pct = pct

    def set_status(self, status):
        with self._lock:
            self.status = status

    def snapshot(self):
        with self._lock:
            speed_normalized = min(100.0, max(0.0, (self.speed_ms / MAX_SPEED_MS) * 100.0))
            return {
                "robot_id": self.robot_id,
                "robot_type": self.robot_type,
                "vendor": self.vendor,
                "facility_id": self.facility_id,
                "timestamp_ms": int(time.time() * 1000),
                "distance_cm": round(self.distance_cm, 1),
                "speed": round(speed_normalized, 1),
                "steering_angle": 0.0,  # differential drive — no steering servo
                "camera_pan": 0.0,
                "camera_tilt": 0.0,
                "grayscale": {"left": 0, "center": 0, "right": 0},
                "battery_voltage": round(self.battery_voltage, 2),
                "status": self.status,
                "position_x": round(self.position_x, 3),
                "position_y": round(self.position_y, 3),
                "heading_deg": round(self.heading_deg, 1),
            }


# ─── Quaternion to Euler (yaw only) ─────────────────────────────────────────

def quaternion_to_yaw_deg(x, y, z, w):
    """Extract yaw from quaternion, return degrees."""
    import numpy as np
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw_rad = np.arctan2(siny_cosp, cosy_cosp)
    return float(np.degrees(yaw_rad))


# ─── ROS 2 Node ──────────────────────────────────────────────────────────────

class ASBRos2Node:
    """ROS 2 node that subscribes to /odom, /scan, /battery_state."""

    def __init__(self, state: TelemetryState):
        import rclpy
        from rclpy.node import Node

        self.state = state
        rclpy.init()
        self.node = Node("asb_telemetry_connector")
        self._cmd_vel_pub = None

        # /odom subscriber
        from nav_msgs.msg import Odometry
        self.node.create_subscription(Odometry, "/odom", self._odom_cb, 10)
        self.node.get_logger().info("Subscribed to /odom")

        # /scan subscriber
        try:
            from sensor_msgs.msg import LaserScan
            self.node.create_subscription(LaserScan, "/scan", self._scan_cb, 10)
            self.node.get_logger().info("Subscribed to /scan")
        except Exception as e:
            self.node.get_logger().warn(f"Could not subscribe to /scan: {e}")

        # /battery_state subscriber (optional)
        try:
            from sensor_msgs.msg import BatteryState
            self.node.create_subscription(BatteryState, "/battery_state", self._battery_cb, 10)
            self.node.get_logger().info("Subscribed to /battery_state")
        except Exception:
            self.node.get_logger().info("/battery_state not available — using default values")

        # cmd_vel publisher for e-stop
        from geometry_msgs.msg import Twist
        self._twist_type = Twist
        self._cmd_vel_pub = self.node.create_publisher(Twist, "/cmd_vel", 10)

    def _odom_cb(self, msg):
        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        twist = msg.twist.twist.linear

        heading = quaternion_to_yaw_deg(ori.x, ori.y, ori.z, ori.w)
        speed = math.sqrt(twist.x ** 2 + twist.y ** 2)

        self.state.update_odom(pos.x, pos.y, heading, speed)

    def _scan_cb(self, msg):
        if not msg.ranges:
            return

        angle_min = msg.angle_min
        angle_inc = msg.angle_increment
        if angle_inc == 0:
            return

        n = len(msg.ranges)
        center_angle = 0.0
        arc_rad = math.radians(FORWARD_ARC_DEG)

        start_angle = center_angle - arc_rad
        end_angle = center_angle + arc_rad

        start_idx = max(0, int((start_angle - angle_min) / angle_inc))
        end_idx = min(n - 1, int((end_angle - angle_min) / angle_inc))

        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx

        min_dist = float("inf")
        for i in range(start_idx, end_idx + 1):
            r = msg.ranges[i]
            if r > 0 and not math.isinf(r) and not math.isnan(r):
                if r < min_dist:
                    min_dist = r

        if math.isinf(min_dist):
            self.state.update_scan(999.0)
        else:
            self.state.update_scan(min_dist * 100.0)

        # Store full scan for LIDAR mapper
        self.state.update_scan_full(
            msg.ranges, msg.angle_min, msg.angle_increment,
            msg.range_min, msg.range_max,
        )

    def _battery_cb(self, msg):
        self.state.update_battery(msg.voltage, msg.percentage * 100.0)

    def send_estop(self):
        """Publish zero-velocity twist to /cmd_vel."""
        if self._cmd_vel_pub is not None:
            stop_msg = self._twist_type()
            self._cmd_vel_pub.publish(stop_msg)
            self.node.get_logger().warn("E-STOP: published zero velocity to /cmd_vel")

    def spin(self):
        import rclpy
        rclpy.spin(self.node)

    def shutdown(self):
        import rclpy
        self.node.destroy_node()
        rclpy.shutdown()


# ─── Mock mode (no ROS 2 required) ──────────────────────────────────────────

class MockRos2Node:
    """Generates synthetic TurtleBot telemetry for testing.
    If boundaries are provided, the robot patrols within its assigned rooms.
    """

    def __init__(self, state: TelemetryState, boundaries=None):
        self.state = state
        self.bounds = boundaries
        self._running = True
        self._t = 0.0

        # Initialize position from boundaries or default
        if self.bounds:
            self._x, self._y = self.bounds.random_point_inside()
            bbox = self.bounds.bbox()
            self._patrol_radius = min(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 0.3
        else:
            self._x, self._y = 0.0, 0.0
            self._patrol_radius = 3.0

        self._heading_rad = random.uniform(0, 2 * math.pi)
        self._speed = 0.35
        # Pick a random target inside boundaries to walk toward
        self._target_x, self._target_y = self._pick_target()

    def _pick_target(self):
        if self.bounds:
            return self.bounds.random_point_inside()
        angle = random.uniform(0, 2 * math.pi)
        return (self._patrol_radius * math.cos(angle),
                self._patrol_radius * math.sin(angle))

    def spin(self):
        """Simulates ROS 2 topic updates at ~20 Hz."""
        while self._running:
            self._t += 0.05
            t = self._t
            dt = 0.05

            # Steer toward target
            dx = self._target_x - self._x
            dy = self._target_y - self._y
            dist_to_target = math.hypot(dx, dy)

            if dist_to_target < 0.3:
                # Reached target, pick a new one
                self._target_x, self._target_y = self._pick_target()
                dx = self._target_x - self._x
                dy = self._target_y - self._y
                dist_to_target = math.hypot(dx, dy)

            # Desired heading toward target
            desired_heading = math.atan2(dy, dx)
            # Smoothly rotate toward target
            angle_diff = desired_heading - self._heading_rad
            angle_diff = math.atan2(math.sin(angle_diff), math.cos(angle_diff))
            max_turn = 1.5 * dt  # ~85 deg/s max turn rate
            self._heading_rad += max(-max_turn, min(max_turn, angle_diff))

            # Speed: oscillate with occasional bursts
            self._speed = 0.35 + 0.15 * math.sin(t * 0.7) + random.gauss(0, 0.02)
            if random.random() < 0.01:
                self._speed = random.uniform(0.7, 1.1)
            self._speed = max(0.05, self._speed)

            # Move
            self._x += math.cos(self._heading_rad) * self._speed * dt
            self._y += math.sin(self._heading_rad) * self._speed * dt

            # Clamp to boundaries
            if self.bounds:
                self._x, self._y = self.bounds.clamp(self._x, self._y)

            heading_deg = math.degrees(self._heading_rad) % 360
            if heading_deg > 180:
                heading_deg -= 360

            self.state.update_odom(self._x, self._y, heading_deg, self._speed)

            # LIDAR: distance based on proximity
            dist = 120 + 80 * math.sin(t * 0.2) + random.gauss(0, 10)
            if random.random() < 0.03:
                dist = random.uniform(8, 25)
            dist = max(0, dist)
            self.state.update_scan(dist)

            # Battery
            drain = t * 0.0002
            voltage = max(10.0, 12.6 - drain + random.gauss(0, 0.03))
            pct = max(0, min(100, ((voltage - 10.0) / 2.6) * 100))
            self.state.update_battery(voltage, pct)

            time.sleep(0.05)

    def send_estop(self):
        print("[mock] E-STOP: would publish zero velocity to /cmd_vel")

    def shutdown(self):
        self._running = False


# ─── WebSocket streaming agent ───────────────────────────────────────────────

class StreamingAgent:
    def __init__(self, state: TelemetryState, ros_node, ws_url: str,
                 rate_hz: int = 10, mapper=None, camera=None):
        self.state = state
        self.ros_node = ros_node
        self.ws_url = ws_url
        self.rate_hz = rate_hz
        self.mapper = mapper
        self.camera = camera
        self._running = True

    def handle_command(self, cmd):
        command = cmd.get("command")
        if command == "estop":
            print("[agent] ESTOP received")
            self.ros_node.send_estop()
            self.state.set_status("estop")
        elif command == "resume":
            print("[agent] RESUME received")
            self.state.set_status("active")
        else:
            print(f"[agent] unknown command: {command}")

    async def send_loop(self, ws):
        interval = 1.0 / self.rate_hz
        while self._running:
            payload = self.state.snapshot()
            await ws.send(json.dumps(payload))

            ts = payload["timestamp_ms"]

            # Feed LIDAR mapper
            if self.mapper:
                scan = self.state.pop_scan()
                if scan:
                    self.mapper.update_from_scan(
                        scan["ranges"], scan["angle_min"], scan["angle_increment"],
                        scan["range_min"], scan["range_max"],
                        self.state.position_x, self.state.position_y,
                        self.state.heading_rad, ts,
                    )
                if self.mapper.should_send(ts):
                    map_msg = self.mapper.get_map_data(
                        self.state.robot_id,
                        self.state.position_x, self.state.position_y,
                        self.state.heading_rad,
                    )
                    await ws.send(json.dumps(map_msg))

            # Camera snapshots
            if self.camera and self.camera.should_snapshot(ts):
                snap = self.camera.capture_snapshot(self.state.robot_id, ts)
                if snap:
                    await ws.send(json.dumps(snap))

            await asyncio.sleep(interval)

    async def recv_loop(self, ws):
        async for message in ws:
            try:
                cmd = json.loads(message)
                self.handle_command(cmd)
            except json.JSONDecodeError:
                print(f"[agent] invalid message: {message}")

    async def run(self):
        backoff = RECONNECT_BASE
        while self._running:
            try:
                print(f"[agent] connecting to {self.ws_url}")
                async with websockets.connect(self.ws_url) as ws:
                    print(f"[agent] connected as {ROBOT_ID} ({ROBOT_TYPE}/{VENDOR})")
                    backoff = RECONNECT_BASE

                    send_task = asyncio.create_task(self.send_loop(ws))
                    recv_task = asyncio.create_task(self.recv_loop(ws))

                    done, pending = await asyncio.wait(
                        [send_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()

            except (
                websockets.ConnectionClosed,
                websockets.InvalidURI,
                OSError,
            ) as e:
                print(f"[agent] disconnected: {e}")
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            print(f"[agent] reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    def shutdown(self):
        print("\n[agent] shutting down...")
        self._running = False


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ROS 2 Fleet Connector for AI Security Brain")
    parser.add_argument("--mock", action="store_true", help="Use synthetic telemetry (no ROS 2)")
    parser.add_argument("--url", default=BACKEND_WS, help=f"Backend WebSocket URL (default: {BACKEND_WS})")
    parser.add_argument("--robot-id", default=ROBOT_ID, help=f"Robot ID (default: {ROBOT_ID})")
    parser.add_argument("--robot-type", default=ROBOT_TYPE, help=f"Robot type (default: {ROBOT_TYPE})")
    parser.add_argument("--vendor", default=VENDOR, help=f"Vendor (default: {VENDOR})")
    parser.add_argument("--facility-id", default=FACILITY_ID, help=f"Facility ID (default: {FACILITY_ID})")
    parser.add_argument("--rate", type=int, default=SAMPLE_RATE_HZ, help=f"Sample rate Hz (default: {SAMPLE_RATE_HZ})")
    parser.add_argument("--no-map", action="store_true", help="Disable LIDAR mapping")
    parser.add_argument("--no-camera", action="store_true", help="Disable camera snapshots")
    parser.add_argument("--grid-size", type=float, default=10.0, help="Map grid size in meters (default: 10)")
    args = parser.parse_args()

    robot_id = args.robot_id
    robot_type = args.robot_type
    vendor = args.vendor
    facility_id = args.facility_id
    rate = args.rate

    state = TelemetryState(robot_id, robot_type, vendor, facility_id)

    if args.mock:
        print(f"[agent] MOCK mode — {robot_id} ({robot_type}/{vendor}) @ {rate} Hz")
        # Fetch movement boundaries from backend
        boundaries = None
        try:
            from robot_boundaries import RobotBoundaries
            api_base = args.url.replace("ws://", "http://").replace("/ws/telemetry", "").split("?")[0]
            boundaries = RobotBoundaries.fetch(api_base, robot_id)
        except Exception as e:
            print(f"[agent] boundaries fetch failed, using defaults: {e}")
        ros_node = MockRos2Node(state, boundaries=boundaries)
    else:
        try:
            print(f"[agent] initializing ROS 2 node for {robot_id}")
            ros_node = ASBRos2Node(state)
        except Exception as e:
            print(f"[agent] failed to initialize ROS 2: {e}")
            print("[agent] hint: use --mock for testing without ROS 2")
            sys.exit(1)

    # Initialize LIDAR mapper
    mapper = None
    if not args.no_map:
        from lidar_mapper import LidarMapper
        mapper = LidarMapper(grid_size_m=args.grid_size, resolution_cm=5)
        print(f"[agent] LIDAR mapper enabled ({args.grid_size}m grid, 5cm resolution)")

    # Initialize camera
    camera = None
    if not args.no_camera:
        from lidar_mapper import ROS2CameraCapture
        camera = ROS2CameraCapture()
        if not args.mock and hasattr(ros_node, "node"):
            camera.start(ros_node.node)

    agent = StreamingAgent(state, ros_node, args.url, rate, mapper=mapper, camera=camera)

    # Run ROS 2 spin in a background thread
    ros_thread = threading.Thread(target=ros_node.spin, daemon=True)
    ros_thread.start()

    # Handle SIGINT
    def on_sigint(_sig, _frame):
        agent.shutdown()
        ros_node.shutdown()

    signal.signal(signal.SIGINT, on_sigint)

    # Run WebSocket streaming in asyncio main thread
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        pass
    finally:
        ros_node.shutdown()
        print("[agent] exited cleanly")


if __name__ == "__main__":
    main()
