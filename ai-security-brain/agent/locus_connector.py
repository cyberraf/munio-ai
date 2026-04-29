"""
Locus Robotics Fleet Connector for AI Security Brain
Polls the Locus fleet management API and streams normalized telemetry
to the ASB backend via WebSocket.

Usage:
  Real API:  python3 locus_connector.py
  Mock mode: python3 locus_connector.py --mock

In mock mode, simulates 3 Locus Origin robots moving around a virtual
warehouse grid (50m x 30m) with realistic movement patterns.
"""

import argparse
import asyncio
import json
import math
import os
import random
import signal
import sys
import time

import websockets

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# ─── Configuration ───────────────────────────────────────────────────────────

LOCUS_API_BASE = os.environ.get("LOCUS_API_BASE", "https://api.locusrobotics.com/v1")
LOCUS_API_KEY = os.environ.get("LOCUS_API_KEY", "your-api-key-here")
BACKEND_WS = os.environ.get("BACKEND_WS", "ws://localhost:8080/ws/telemetry?key=asb-robot-key-2026")
FACILITY_ID = os.environ.get("FACILITY_ID", "warehouse-001")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3"))
VENDOR = "locus"
ROBOT_TYPE = "locus_origin"
MAX_SPEED_MS = 2.0  # Locus Origin max ~2 m/s; scale 0–2 → 0–100
RECONNECT_BASE = 2
RECONNECT_MAX = 10


# ─── Locus status → ASB status mapping ──────────────────────────────────────

def map_locus_status(locus_status: str) -> str:
    """Map Locus robot states to ASB status."""
    s = locus_status.lower()
    if s in ("working", "driving", "picking", "transporting", "navigating"):
        return "active"
    if s in ("idle", "waiting", "charging", "docked"):
        return "idle"
    if s in ("error", "fault", "stuck", "estopped", "disconnected"):
        return "estop"
    return "idle"


# ─── Real Locus API client ──────────────────────────────────────────────────

class LocusAPIClient:
    """Polls the Locus Robotics fleet API."""

    def __init__(self, base_url: str, api_key: str, facility_id: str = ""):
        if not HAS_HTTPX:
            raise ImportError("httpx is required for real Locus API mode. pip install httpx")
        self.base = base_url.rstrip("/")
        self.facility_id = facility_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        self.client = httpx.AsyncClient(headers=self.headers, timeout=10)

    async def get_fleet(self) -> list[dict]:
        """Fetch all robots from the fleet."""
        resp = await self.client.get(f"{self.base}/fleet/robots")
        resp.raise_for_status()
        data = resp.json()
        # Adapt to whatever structure Locus returns
        robots = data if isinstance(data, list) else data.get("robots", data.get("data", []))
        return robots

    async def get_robot_position(self, robot_id: str) -> dict | None:
        """Fetch position for a specific robot."""
        try:
            resp = await self.client.get(f"{self.base}/fleet/robots/{robot_id}/position")
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    async def get_robot_status(self, robot_id: str) -> dict | None:
        """Fetch detailed status for a specific robot."""
        try:
            resp = await self.client.get(f"{self.base}/fleet/robots/{robot_id}/status")
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def normalize_robot(self, raw: dict, position: dict | None, status: dict | None) -> dict:
        """Normalize Locus API response to ASB telemetry schema."""
        rid = raw.get("id") or raw.get("robot_id") or raw.get("name", "unknown")
        robot_id = f"locus-{rid}" if not str(rid).startswith("locus-") else rid

        # Position
        pos_x, pos_y = 0.0, 0.0
        if position:
            pos_x = float(position.get("x", position.get("position_x", 0)))
            pos_y = float(position.get("y", position.get("position_y", 0)))
        elif "position" in raw:
            p = raw["position"]
            pos_x = float(p.get("x", 0))
            pos_y = float(p.get("y", 0))

        # Speed
        speed_ms = 0.0
        if "velocity" in raw:
            v = raw["velocity"]
            if isinstance(v, (int, float)):
                speed_ms = float(v)
            elif isinstance(v, dict):
                vx = float(v.get("x", 0))
                vy = float(v.get("y", 0))
                speed_ms = math.sqrt(vx ** 2 + vy ** 2)
        elif "speed" in raw:
            speed_ms = float(raw["speed"])
        speed_normalized = min(100.0, max(0.0, (speed_ms / MAX_SPEED_MS) * 100.0))

        # Heading
        heading = 0.0
        if "heading" in raw:
            heading = float(raw["heading"])
        elif "orientation" in raw:
            heading = float(raw["orientation"])

        # Status
        locus_status = raw.get("status", raw.get("state", "idle"))
        if status and "status" in status:
            locus_status = status["status"]
        asb_status = map_locus_status(str(locus_status))

        # Battery
        battery_v = 0.0
        if "battery" in raw:
            b = raw["battery"]
            if isinstance(b, dict):
                battery_v = float(b.get("voltage", 0))
            else:
                battery_v = float(b)

        # Faults (for ROBOT_FAULT events — encoded in status field)
        has_fault = False
        if status:
            faults = status.get("faults", status.get("errors", []))
            if faults:
                has_fault = True
                asb_status = "estop"

        return {
            "robot_id": robot_id,
            "robot_type": ROBOT_TYPE,
            "vendor": VENDOR,
            "facility_id": self.facility_id,
            "timestamp_ms": int(time.time() * 1000),
            "distance_cm": 999.0,  # No proximity sensor from fleet API
            "speed": round(speed_normalized, 1),
            "steering_angle": 0.0,
            "camera_pan": 0.0,
            "camera_tilt": 0.0,
            "grayscale": {"left": 0, "center": 0, "right": 0},
            "battery_voltage": round(battery_v, 2),
            "status": asb_status,
            "position_x": round(pos_x, 3),
            "position_y": round(pos_y, 3),
            "heading_deg": round(heading, 1),
            "_has_fault": has_fault,
        }

    async def poll(self) -> list[dict]:
        """Poll fleet and return normalized telemetry for all robots."""
        fleet = await self.get_fleet()
        results = []
        for raw in fleet:
            rid = raw.get("id") or raw.get("robot_id") or raw.get("name")
            position = await self.get_robot_position(rid) if rid else None
            status = await self.get_robot_status(rid) if rid else None
            results.append(self.normalize_robot(raw, position, status))
        return results

    async def close(self):
        await self.client.aclose()


# ─── Mock fleet simulator ───────────────────────────────────────────────────

class MockLocusFleet:
    """Simulates Locus robots moving within their assigned room boundaries."""

    def __init__(self, robot_count: int = 3, facility_id: str = "", api_base: str = ""):
        self.facility_id = facility_id
        self.robots = []
        for i in range(1, robot_count + 1):
            robot_id = f"locus-{i:03d}"
            # Fetch boundaries for each robot
            bounds = None
            if api_base:
                try:
                    from robot_boundaries import RobotBoundaries
                    bounds = RobotBoundaries.fetch(api_base, robot_id)
                except Exception as e:
                    print(f"[mock] boundaries fetch failed for {robot_id}: {e}")

            if bounds:
                sx, sy = bounds.random_point_inside()
                bbox = bounds.bbox()
            else:
                sx = random.uniform(5, 45)
                sy = random.uniform(5, 25)
                bbox = (0, 0, 50, 30)

            self.robots.append({
                "id": robot_id,
                "x": sx,
                "y": sy,
                "vx": random.uniform(-0.5, 0.5),
                "vy": random.uniform(-0.5, 0.5),
                "heading": random.uniform(0, 360),
                "speed_target": random.uniform(0.3, 0.8),
                "status": "active",
                "battery_v": 25.0 + random.uniform(-0.5, 0.5),
                "fault_timer": 0,
                "bounds": bounds,
                "bbox": bbox,
            })

    async def poll(self) -> list[dict]:
        """Simulate one tick and return telemetry for all robots."""
        results = []
        for r in self.robots:
            self._tick_robot(r)
            speed_ms = math.sqrt(r["vx"] ** 2 + r["vy"] ** 2)
            speed_norm = min(100.0, max(0.0, (speed_ms / MAX_SPEED_MS) * 100.0))

            results.append({
                "robot_id": r["id"],
                "robot_type": ROBOT_TYPE,
                "vendor": VENDOR,
                "facility_id": self.facility_id,
                "timestamp_ms": int(time.time() * 1000),
                "distance_cm": 999.0,
                "speed": round(speed_norm, 1),
                "steering_angle": 0.0,
                "camera_pan": 0.0,
                "camera_tilt": 0.0,
                "grayscale": {"left": 0, "center": 0, "right": 0},
                "battery_voltage": round(r["battery_v"], 2),
                "status": r["status"],
                "position_x": round(r["x"], 3),
                "position_y": round(r["y"], 3),
                "heading_deg": round(r["heading"], 1),
                "_has_fault": r["status"] == "estop",
            })
        return results

    def _tick_robot(self, r: dict):
        """Advance one robot's state by one poll tick."""
        dt = POLL_INTERVAL

        # Fault handling
        if r["fault_timer"] > 0:
            r["fault_timer"] -= dt
            if r["fault_timer"] <= 0:
                r["status"] = "active"
                r["fault_timer"] = 0
            return

        # 0.5% chance of fault per tick
        if random.random() < 0.005:
            r["status"] = "estop"
            r["fault_timer"] = random.uniform(10, 30)
            r["vx"] = 0
            r["vy"] = 0
            return

        # 5% chance of going idle (waiting at station)
        if r["status"] == "idle":
            if random.random() < 0.1:
                r["status"] = "active"
                r["speed_target"] = random.uniform(0.3, 0.8)
            r["vx"] = 0
            r["vy"] = 0
            return
        if random.random() < 0.05:
            r["status"] = "idle"
            r["vx"] = 0
            r["vy"] = 0
            return

        # Movement with momentum and random direction changes
        if random.random() < 0.15:
            angle = random.uniform(0, 2 * math.pi)
            speed = r["speed_target"] + random.gauss(0, 0.1)
            speed = max(0.1, min(1.5, speed))
            r["vx"] = math.cos(angle) * speed
            r["vy"] = math.sin(angle) * speed

        # Occasional speed spike (approaching another robot quickly)
        if random.random() < 0.01:
            r["vx"] *= 2.5
            r["vy"] *= 2.5

        r["x"] += r["vx"] * dt
        r["y"] += r["vy"] * dt

        # Boundary enforcement
        bounds = r.get("bounds")
        if bounds:
            if not bounds.contains(r["x"], r["y"]):
                r["x"], r["y"] = bounds.clamp(r["x"], r["y"])
                r["vx"] *= -1
                r["vy"] *= -1
        else:
            bbox = r.get("bbox", (0, 0, 50, 30))
            margin = 1
            if r["x"] < bbox[0] + margin or r["x"] > bbox[2] - margin:
                r["vx"] *= -1
                r["x"] = max(bbox[0] + margin, min(bbox[2] - margin, r["x"]))
            if r["y"] < bbox[1] + margin or r["y"] > bbox[3] - margin:
                r["vy"] *= -1
                r["y"] = max(bbox[1] + margin, min(bbox[3] - margin, r["y"]))

        # Update heading from velocity
        if abs(r["vx"]) > 0.01 or abs(r["vy"]) > 0.01:
            r["heading"] = math.degrees(math.atan2(r["vy"], r["vx"])) % 360

        # Battery drain
        r["battery_v"] -= 0.0001 * dt
        r["battery_v"] = max(20.0, r["battery_v"])

    async def close(self):
        pass


# ─── Fleet streaming agent ──────────────────────────────────────────────────

class FleetStreamingAgent:
    """Polls a fleet source and streams all robot telemetry to ASB backend."""

    def __init__(self, fleet_source, ws_url: str, poll_interval: int):
        self.fleet = fleet_source
        self.ws_url = ws_url
        self.poll_interval = poll_interval
        self._running = True
        self._ws = None

    def handle_command(self, cmd):
        command = cmd.get("command")
        robot_id = cmd.get("robot_id", "")
        print(f"[agent] command: {command} → {robot_id or 'all'}")
        # Fleet API robots don't support direct e-stop via WebSocket.
        # Commands would need to go through the Locus API. Log for now.

    async def run(self):
        backoff = RECONNECT_BASE
        while self._running:
            try:
                print(f"[agent] connecting to {self.ws_url}")
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    print(f"[agent] connected — polling fleet every {self.poll_interval}s")
                    backoff = RECONNECT_BASE

                    poll_task = asyncio.create_task(self._poll_loop(ws))
                    recv_task = asyncio.create_task(self._recv_loop(ws))

                    done, pending = await asyncio.wait(
                        [poll_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()

            except (websockets.ConnectionClosed, websockets.InvalidURI, OSError) as e:
                print(f"[agent] disconnected: {e}")
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            print(f"[agent] reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _poll_loop(self, ws):
        """Poll fleet and send telemetry for each robot."""
        while self._running:
            try:
                telemetry_list = await self.fleet.poll()
                for telemetry in telemetry_list:
                    # Remove internal fields before sending
                    payload = {k: v for k, v in telemetry.items() if not k.startswith("_")}
                    await ws.send(json.dumps(payload))

                robot_ids = [t["robot_id"] for t in telemetry_list]
                statuses = [t["status"] for t in telemetry_list]
                print(f"[agent] polled {len(telemetry_list)} robots: {', '.join(f'{r}({s})' for r, s in zip(robot_ids, statuses))}")

            except Exception as e:
                print(f"[agent] poll error: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _recv_loop(self, ws):
        """Listen for commands from backend."""
        async for message in ws:
            try:
                cmd = json.loads(message)
                self.handle_command(cmd)
            except json.JSONDecodeError:
                print(f"[agent] invalid message: {message}")

    def shutdown(self):
        print("\n[agent] shutting down...")
        self._running = False


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Locus Robotics Fleet Connector for AI Security Brain")
    parser.add_argument("--mock", action="store_true", help="Simulate fleet (no real Locus API)")
    parser.add_argument("--url", default=BACKEND_WS, help=f"Backend WebSocket URL (default: {BACKEND_WS})")
    parser.add_argument("--facility-id", default=FACILITY_ID, help=f"Facility ID (default: {FACILITY_ID})")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL, help=f"Poll interval in seconds (default: {POLL_INTERVAL})")
    parser.add_argument("--robot-count", type=int, default=3, help="Number of mock robots (default: 3)")
    args = parser.parse_args()

    facility_id = args.facility_id
    poll_interval = args.poll_interval

    if args.mock:
        print(f"[agent] MOCK mode — simulating {args.robot_count} Locus robots in {facility_id}")
        api_base = args.url.replace("ws://", "http://").replace("/ws/telemetry", "").split("?")[0]
        fleet = MockLocusFleet(robot_count=args.robot_count, facility_id=facility_id, api_base=api_base)
    else:
        if not HAS_HTTPX:
            print("[agent] httpx is required for real API mode. Install: pip install httpx")
            sys.exit(1)
        print(f"[agent] connecting to Locus API at {LOCUS_API_BASE}")
        fleet = LocusAPIClient(LOCUS_API_BASE, LOCUS_API_KEY, facility_id=facility_id)

    agent = FleetStreamingAgent(fleet, args.url, poll_interval)

    def on_sigint(_sig, _frame):
        agent.shutdown()

    signal.signal(signal.SIGINT, on_sigint)

    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(fleet.close())
        print("[agent] exited cleanly")


if __name__ == "__main__":
    main()
