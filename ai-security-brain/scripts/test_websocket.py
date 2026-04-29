"""
WebSocket stream test -- connects to /ws/live and /ws/alerts,
prints received messages, then exits.

Usage: python3 scripts/test_websocket.py
  (requires the backend + mock agent to be running)
"""

import asyncio
import json
import sys

import websockets

LIVE_URL = "ws://localhost:8080/ws/live"
ALERTS_URL = "ws://localhost:8080/ws/alerts"


async def stream_live(count=10):
    """Connect to /ws/live and print the first `count` telemetry events."""
    print("")
    print("-" * 60)
    print(f"  Connecting to {LIVE_URL}")
    print(f"  Waiting for {count} telemetry events...")
    print("-" * 60)
    print("")

    received = 0
    try:
        async with websockets.connect(LIVE_URL) as ws:
            while received < count:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(msg)
                received += 1
                dist = data.get("distance_cm", "?")
                speed = data.get("speed", "?")
                status = data.get("status", "?")
                gs = data.get("grayscale", {})
                print(
                    f"  [{received:2d}/{count}] "
                    f"dist={dist:>6}cm  speed={speed:>5}  "
                    f"status={status:<6}  "
                    f"gs=[{gs.get('left','?')},{gs.get('center','?')},{gs.get('right','?')}]"
                )
    except asyncio.TimeoutError:
        print(f"  timeout after {received} events")
    except websockets.ConnectionClosed as e:
        print(f"  connection closed: {e}")

    print(f"\n  Received {received} telemetry events")
    return received


async def stream_alerts(timeout_s=30):
    """Connect to /ws/alerts and print events until timeout."""
    print("")
    print("-" * 60)
    print(f"  Connecting to {ALERTS_URL}")
    print(f"  Waiting up to {timeout_s}s for alert events...")
    print("-" * 60)
    print("")

    received = 0
    try:
        async with websockets.connect(ALERTS_URL) as ws:
            deadline = asyncio.get_event_loop().time() + timeout_s
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                data = json.loads(msg)
                received += 1
                print(
                    f"  [{received}] "
                    f"{data.get('severity','?'):>8} {data.get('event_type','?'):<20} "
                    f"dist={data.get('distance_cm','?')}cm  "
                    f"speed={data.get('speed','?')}  "
                    f"-- {data.get('description','')}"
                )
    except asyncio.TimeoutError:
        pass
    except websockets.ConnectionClosed as e:
        print(f"  connection closed: {e}")

    print(f"\n  Received {received} alert events in {timeout_s}s")
    return received


async def main():
    print("=" * 60)
    print("  AI Security Brain -- WebSocket Stream Test")
    print("=" * 60)

    live_count = await stream_live(10)
    alert_count = await stream_alerts(30)

    print("")
    print("=" * 60)
    print(f"  Summary: {live_count} telemetry, {alert_count} alerts")
    ok = live_count >= 10 and alert_count >= 1
    if ok:
        print("  PASSED")
    else:
        if live_count < 10:
            print(f"  FAIL: expected >=10 telemetry, got {live_count}")
        if alert_count < 1:
            print(f"  WARN: no alerts received (mock data is random -- may be OK)")
    print("=" * 60)
    print("")

    return 0 if ok else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
