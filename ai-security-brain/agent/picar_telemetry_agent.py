"""
PiCar-X Telemetry Agent
Streams sensor data to the AI Security Brain backend via WebSocket at 10 Hz.

Usage:
  On Pi:     python3 picar_telemetry_agent.py
  Mock mode: python3 picar_telemetry_agent.py --mock
"""

import argparse
import asyncio
import json
import math
import random
import sys
import time
from collections import deque

import websockets

# ─── Configuration ───────────────────────────────────────────────────────────
BACKEND_WS = "ws://192.168.1.100:8080/ws/telemetry?key=asb-robot-key-2026"
ROBOT_ID = "picar-1"
SEND_HZ = 10
SMOOTHING_WINDOW = 5
RECONNECT_BASE = 2
RECONNECT_MAX = 10


# ─── Sensor abstraction ─────────────────────────────────────────────────────

class PiCarSensors:
    """Reads real PiCar-X hardware sensors."""

    def __init__(self):
        from picarx import Picarx
        from robot_hat import ADC, Pin, Ultrasonic

        self.px = Picarx()
        self.ultrasonic = Ultrasonic(Pin("D2"), Pin("D3"))
        self.adc_battery = ADC("A4")

    def read_distance(self):
        return self.ultrasonic.read()

    def read_grayscale(self):
        data = self.px.get_grayscale_data()
        return data[0], data[1], data[2]

    def read_speed(self):
        return self.px.current_speed

    def read_steering(self):
        return self.px.dir_current_angle

    def read_camera_pan(self):
        return 0.0

    def read_camera_tilt(self):
        return 0.0

    def read_battery(self):
        return self.adc_battery.read_voltage()

    def stop(self):
        self.px.forward(0)

    def estop(self):
        self.px.forward(0)
        self.px.set_dir_servo_angle(0)


class MockSensors:
    """Generates synthetic telemetry for testing without hardware."""

    def __init__(self):
        self._t = 0.0
        self._speed = 40.0
        self._steering = 0.0
        self._estop = False

    def read_distance(self):
        self._t += 0.1
        # Oscillate 15–80 cm with occasional close approaches
        base = 47.5 + 32.5 * math.sin(self._t * 0.3)
        noise = random.gauss(0, 2)
        # 3% chance of a very close reading
        if random.random() < 0.03:
            return round(random.uniform(5, 14), 1)
        # 1% chance of sensor error
        if random.random() < 0.01:
            return -1
        return round(max(0, base + noise), 1)

    def read_grayscale(self):
        base = 800
        # Occasional path deviation burst
        if random.random() < 0.02:
            return 1600, 1650, 1580
        return (
            base + random.randint(-100, 100),
            base + random.randint(-100, 100),
            base + random.randint(-100, 100),
        )

    def read_speed(self):
        if self._estop:
            return 0.0
        # Jitter around 40
        self._speed = max(0, min(100, 40 + random.gauss(0, 5)))
        # 2% chance of speed spike
        if random.random() < 0.02:
            self._speed = random.uniform(65, 85)
        return round(self._speed, 1)

    def read_steering(self):
        if self._estop:
            return 0.0
        self._steering = max(-40, min(40, random.gauss(0, 10)))
        return round(self._steering, 1)

    def read_camera_pan(self):
        return round(random.gauss(0, 3), 1)

    def read_camera_tilt(self):
        return round(random.gauss(0, 2), 1)

    def read_battery(self):
        # Slow drain from 7.4
        drain = self._t * 0.0001
        return round(max(5.0, 7.4 - drain + random.gauss(0, 0.05)), 2)

    def stop(self):
        self._speed = 0

    def estop(self):
        self._estop = True
        self._speed = 0
        self._steering = 0

    def resume(self):
        self._estop = False


# ─── Moving average filter ───────────────────────────────────────────────────

class MovingAverage:
    def __init__(self, size):
        self._buf = deque(maxlen=size)

    def add(self, value):
        """Add a value. Returns smoothed average. Pass -1 to skip (sensor error)."""
        if value < 0:
            return -1.0
        self._buf.append(value)
        return sum(self._buf) / len(self._buf)

    def raw_with_smoothed(self, raw_value):
        """Returns (value_to_send, smoothed). Sends -1 on error, smoothed otherwise."""
        if raw_value < 0:
            # Don't add to buffer, but still return -1 so backend detects failure
            return -1.0
        self._buf.append(raw_value)
        return round(sum(self._buf) / len(self._buf), 2)


# ─── Agent ───────────────────────────────────────────────────────────────────

class TelemetryAgent:
    def __init__(self, sensors, ws_url, robot_id, mapper=None, camera=None):
        self.sensors = sensors
        self.ws_url = ws_url
        self.robot_id = robot_id
        self.status = "active"
        self.distance_filter = MovingAverage(SMOOTHING_WINDOW)
        self.mapper = mapper
        self.camera = camera
        self._running = True

    def build_payload(self):
        raw_dist = self.sensors.read_distance()
        distance = self.distance_filter.raw_with_smoothed(raw_dist)
        gs_l, gs_c, gs_r = self.sensors.read_grayscale()

        return {
            "robot_id": self.robot_id,
            "timestamp_ms": int(time.time() * 1000),
            "distance_cm": distance,
            "speed": self.sensors.read_speed(),
            "steering_angle": self.sensors.read_steering(),
            "camera_pan": self.sensors.read_camera_pan(),
            "camera_tilt": self.sensors.read_camera_tilt(),
            "grayscale": {"left": gs_l, "center": gs_c, "right": gs_r},
            "battery_voltage": self.sensors.read_battery(),
            "status": self.status,
        }

    def handle_command(self, cmd):
        command = cmd.get("command")
        if command == "estop":
            print("[agent] ESTOP received — stopping motors")
            self.sensors.estop()
            self.status = "estop"
        elif command == "resume":
            print("[agent] RESUME received — resuming")
            if hasattr(self.sensors, "resume"):
                self.sensors.resume()
            self.status = "active"
        else:
            print(f"[agent] unknown command: {command}")

    async def send_loop(self, ws):
        """Sends telemetry at SEND_HZ, plus map updates every 2s."""
        interval = 1.0 / SEND_HZ
        while self._running:
            payload = self.build_payload()
            await ws.send(json.dumps(payload))

            ts = payload["timestamp_ms"]

            # Feed the mapper
            if self.mapper:
                self.mapper.update(
                    payload["speed"],
                    payload["steering_angle"],
                    payload["distance_cm"],
                    ts,
                )
                if self.mapper.should_send(ts):
                    map_msg = self.mapper.get_map_data()
                    map_msg["robot_id"] = self.robot_id
                    await ws.send(json.dumps(map_msg))

            # Camera snapshots every 10s
            if self.camera and self.camera.should_snapshot(ts):
                snap = self.camera.get_snapshot_message(self.robot_id, ts)
                if snap:
                    await ws.send(json.dumps(snap))

            await asyncio.sleep(interval)

    async def recv_loop(self, ws):
        """Listens for commands from the backend."""
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
                    print("[agent] connected")
                    backoff = RECONNECT_BASE

                    send_task = asyncio.create_task(self.send_loop(ws))
                    recv_task = asyncio.create_task(self.recv_loop(ws))

                    # Wait until either task finishes (disconnect or error)
                    done, pending = await asyncio.wait(
                        [send_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    # Re-raise exceptions from completed tasks
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
        self.sensors.stop()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PiCar-X Telemetry Agent")
    parser.add_argument(
        "--mock", action="store_true", help="Use synthetic sensor data (no hardware)"
    )
    parser.add_argument(
        "--url", default=BACKEND_WS, help=f"Backend WebSocket URL (default: {BACKEND_WS})"
    )
    parser.add_argument(
        "--robot-id", default=ROBOT_ID, help=f"Robot ID (default: {ROBOT_ID})"
    )
    parser.add_argument(
        "--no-map", action="store_true", help="Disable 2D mapping"
    )
    parser.add_argument(
        "--no-camera", action="store_true", help="Disable camera snapshots"
    )
    args = parser.parse_args()

    if args.mock:
        print("[agent] running in MOCK mode (synthetic data)")
        sensors = MockSensors()
    else:
        try:
            sensors = PiCarSensors()
        except Exception as e:
            print(f"[agent] failed to initialize PiCar-X hardware: {e}")
            print("[agent] hint: use --mock for testing without hardware")
            sys.exit(1)

    # Fetch movement boundaries from backend
    boundaries = None
    if args.mock:
        try:
            from robot_boundaries import RobotBoundaries
            api_base = args.url.replace("ws://", "http://").replace("/ws/telemetry", "").split("?")[0]
            boundaries = RobotBoundaries.fetch(api_base, args.robot_id)
        except Exception as e:
            print(f"[agent] boundaries fetch failed, using defaults: {e}")

    # Initialize mapper
    mapper = None
    if not args.no_map:
        from picar_mapper import PicarMapper
        mapper = PicarMapper(grid_size_m=5.0, resolution_cm=5, boundaries=boundaries)
        print("[agent] 2D mapper enabled (5m grid, 5cm resolution)")

    # Initialize camera
    camera = None
    if not args.no_camera:
        from picar_mapper import CameraMapper
        camera = CameraMapper()

    agent = TelemetryAgent(sensors, args.url, args.robot_id, mapper=mapper, camera=camera)

    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()
