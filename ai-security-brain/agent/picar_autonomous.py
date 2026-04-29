"""
PiCar-X Autonomous Obstacle Avoidance
Simple demo: drives forward, avoids obstacles using ultrasonic sensor.

Run alongside the telemetry agent:
  Terminal 1: python3 picar_telemetry_agent.py
  Terminal 2: python3 picar_autonomous.py

The telemetry agent only reads sensors; this script only controls motors.
No conflict.
"""

import argparse
import math
import random
import sys
import time

# ─── Configuration ───────────────────────────────────────────────────────────
CRUISE_SPEED = 40
SLOW_SPEED = 30
REVERSE_SPEED = 30
TURN_ANGLE = 35
REVERSE_DURATION = 0.5
LOOP_HZ = 20


# ─── Hardware abstraction ───────────────────────────────────────────────────

class PiCarDriver:
    """Controls the real PiCar-X motors and reads ultrasonic."""

    def __init__(self):
        from picarx import Picarx
        from robot_hat import Pin, Ultrasonic

        self.px = Picarx()
        self.ultrasonic = Ultrasonic(Pin("D2"), Pin("D3"))

    def read_distance(self):
        d = self.ultrasonic.read()
        return d if d > 0 else 999  # treat errors as "no obstacle"

    def forward(self, speed):
        self.px.forward(speed)

    def backward(self, speed):
        self.px.backward(speed)

    def set_steering(self, angle):
        self.px.set_dir_servo_angle(angle)

    def stop(self):
        self.px.forward(0)
        self.px.set_dir_servo_angle(0)


class MockDriver:
    """Simulates driving for testing without hardware."""

    def __init__(self):
        self._t = 0.0
        self._speed = 0
        self._angle = 0

    def read_distance(self):
        self._t += 0.05
        # Simulate varying distance with occasional close objects
        base = 50 + 30 * math.sin(self._t * 0.5)
        noise = random.gauss(0, 3)
        if random.random() < 0.05:
            return round(random.uniform(5, 12), 1)
        return round(max(1, base + noise), 1)

    def forward(self, speed):
        self._speed = speed

    def backward(self, speed):
        self._speed = -speed

    def set_steering(self, angle):
        self._angle = angle

    def stop(self):
        self._speed = 0
        self._angle = 0

    def status(self):
        return f"speed={self._speed:+3d}  steering={self._angle:+3d}"


# ─── Main loop ──────────────────────────────────────────────────────────────

def run(driver, mock=False):
    interval = 1.0 / LOOP_HZ
    print("[drive] autonomous mode started")
    print(f"[drive] cruise={CRUISE_SPEED}  slow={SLOW_SPEED}  turn={TURN_ANGLE}°")

    try:
        while True:
            distance = driver.read_distance()

            if distance > 35:
                # Clear path — drive straight
                driver.set_steering(0)
                driver.forward(CRUISE_SPEED)
                state = "CRUISE"
            elif distance >= 15:
                # Getting close — slow down and turn right
                driver.set_steering(TURN_ANGLE)
                driver.forward(SLOW_SPEED)
                state = "AVOID"
            else:
                # Too close — reverse and turn left
                driver.set_steering(-TURN_ANGLE)
                driver.backward(REVERSE_SPEED)
                state = "REVERSE"
                if mock:
                    print(f"[drive] {state}  dist={distance:5.1f}cm  {driver.status()}")
                time.sleep(REVERSE_DURATION)
                driver.forward(0)
                time.sleep(0.1)
                continue

            if mock:
                print(f"[drive] {state:7s}  dist={distance:5.1f}cm  {driver.status()}")

            time.sleep(interval)

    except KeyboardInterrupt:
        pass
    finally:
        print("\n[drive] stopping motors")
        driver.stop()


def main():
    parser = argparse.ArgumentParser(description="PiCar-X Autonomous Obstacle Avoidance")
    parser.add_argument(
        "--mock", action="store_true", help="Simulate driving (no hardware)"
    )
    args = parser.parse_args()

    if args.mock:
        print("[drive] running in MOCK mode")
        driver = MockDriver()
    else:
        try:
            driver = PiCarDriver()
        except Exception as e:
            print(f"[drive] failed to initialize PiCar-X: {e}")
            print("[drive] hint: use --mock for testing without hardware")
            sys.exit(1)

    run(driver, mock=args.mock)


if __name__ == "__main__":
    main()
