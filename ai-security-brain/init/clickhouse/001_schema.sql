CREATE TABLE IF NOT EXISTS telemetry (
  robot_id       String,
  timestamp      DateTime64(3),
  distance_cm    Float32,
  speed          Float32,
  steering_angle Float32,
  camera_pan     Float32,
  camera_tilt    Float32,
  grayscale_l    UInt16,
  grayscale_c    UInt16,
  grayscale_r    UInt16,
  battery_voltage Float32,
  status         LowCardinality(String)
) ENGINE = MergeTree()
ORDER BY (robot_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY;

CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_hourly_mv
ENGINE = SummingMergeTree()
ORDER BY (robot_id, hour)
AS SELECT
  robot_id,
  toStartOfHour(timestamp) AS hour,
  count()                  AS event_count,
  min(distance_cm)         AS min_distance,
  avg(distance_cm)         AS avg_distance,
  avg(speed)               AS avg_speed,
  max(speed)               AS max_speed
FROM telemetry
GROUP BY robot_id, hour;
