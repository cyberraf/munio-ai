CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Robot Platforms (catalog of supported robot types) ──────────────────────
CREATE TABLE robot_platforms (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  manufacturer    TEXT NOT NULL,
  category        TEXT NOT NULL DEFAULT 'ground',
  sensors         JSONB NOT NULL DEFAULT '[]',
  default_thresholds JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ DEFAULT now()
);

INSERT INTO robot_platforms (id, name, manufacturer, category, sensors, default_thresholds) VALUES
  ('sunfounder-picarx', 'PiCar-X', 'SunFounder', 'ground', '[
    {"key": "ultrasonic", "name": "Ultrasonic Distance", "unit": "cm", "type": "range"},
    {"key": "grayscale", "name": "Grayscale Line Sensor", "unit": "", "type": "array", "channels": 3},
    {"key": "camera_servo", "name": "Camera Servo (Pan/Tilt)", "unit": "deg", "type": "servo"},
    {"key": "battery_adc", "name": "Battery ADC", "unit": "V", "type": "voltage"},
    {"key": "motor_encoder", "name": "Motor Speed", "unit": "rpm", "type": "speed"}
  ]', '{
    "proximity_cm": 30,
    "speed_max": 60,
    "off_path_grayscale": 1500,
    "low_battery_v": 6.0
  }'),
  ('dji-robomaster-s1', 'RoboMaster S1', 'DJI', 'ground', '[
    {"key": "tof", "name": "ToF Distance", "unit": "mm", "type": "range"},
    {"key": "imu", "name": "IMU (6-axis)", "unit": "", "type": "imu"},
    {"key": "gimbal", "name": "Gimbal (Pan/Tilt)", "unit": "deg", "type": "servo"},
    {"key": "armor_hit", "name": "Armor Hit Detection", "unit": "", "type": "event"},
    {"key": "battery", "name": "Smart Battery", "unit": "V", "type": "voltage"},
    {"key": "mecanum_encoder", "name": "Mecanum Wheel Encoders", "unit": "rpm", "type": "speed"}
  ]', '{
    "proximity_cm": 50,
    "speed_max": 80,
    "off_path_grayscale": 0,
    "low_battery_v": 10.0
  }'),
  ('turtlebot3-burger', 'TurtleBot3 Burger', 'ROBOTIS', 'ground', '[
    {"key": "lidar", "name": "360 LIDAR", "unit": "m", "type": "lidar"},
    {"key": "imu", "name": "IMU (9-axis)", "unit": "", "type": "imu"},
    {"key": "odometry", "name": "Wheel Odometry", "unit": "m/s", "type": "speed"},
    {"key": "battery", "name": "LiPo Battery", "unit": "V", "type": "voltage"},
    {"key": "bumper", "name": "Bumper Sensors", "unit": "", "type": "event"}
  ]', '{
    "proximity_cm": 40,
    "speed_max": 22,
    "off_path_grayscale": 0,
    "low_battery_v": 11.1
  }'),
  ('generic', 'Generic Robot', 'Custom', 'ground', '[
    {"key": "distance", "name": "Distance Sensor", "unit": "cm", "type": "range"},
    {"key": "speed", "name": "Speed Sensor", "unit": "", "type": "speed"},
    {"key": "battery", "name": "Battery", "unit": "V", "type": "voltage"}
  ]', '{
    "proximity_cm": 30,
    "speed_max": 60,
    "off_path_grayscale": 0,
    "low_battery_v": 6.0
  }');

-- ─── Robot Instances ─────────────────────────────────────────────────────────
CREATE TABLE robots (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  platform_id TEXT NOT NULL REFERENCES robot_platforms(id),
  name        TEXT NOT NULL UNIQUE,
  description TEXT DEFAULT '',
  auth_token  UUID NOT NULL DEFAULT gen_random_uuid(),
  ip_address  TEXT DEFAULT '',
  mac_address TEXT DEFAULT '',
  status      TEXT DEFAULT 'offline',
  thresholds  JSONB,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX idx_robots_auth_token ON robots(auth_token);

-- ─── Incidents ───────────────────────────────────────────────────────────────
CREATE TABLE incidents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  robot_id        TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  severity        TEXT NOT NULL,
  description     TEXT,
  distance_cm     FLOAT,
  speed           FLOAT,
  steering_angle  FLOAT,
  occurred_at     TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_incidents_time ON incidents(occurred_at DESC);
CREATE INDEX idx_incidents_type ON incidents(event_type, occurred_at DESC);
CREATE INDEX idx_incidents_severity ON incidents(severity, occurred_at DESC);

-- ─── Config ──────────────────────────────────────────────────────────────────
CREATE TABLE config (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

INSERT INTO config (key, value) VALUES
  ('thresholds', '{
    "proximity_cm": 30,
    "speed_max": 60,
    "off_path_grayscale": 1500,
    "low_battery_v": 6.0
  }');
