-- Multi-robot support: add vendor, facility, and position columns
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS robot_type LowCardinality(String) DEFAULT '';
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS vendor LowCardinality(String) DEFAULT '';
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS facility_id LowCardinality(String) DEFAULT '';
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS position_x Float64 DEFAULT 0;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS position_y Float64 DEFAULT 0;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS heading_deg Float32 DEFAULT 0;

-- Hourly metrics per facility
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_facility_hourly_mv
ENGINE = SummingMergeTree()
ORDER BY (facility_id, hour)
AS SELECT
  facility_id,
  toStartOfHour(timestamp) AS hour,
  count()                  AS event_count,
  min(distance_cm)         AS min_distance,
  avg(distance_cm)         AS avg_distance,
  avg(speed)               AS avg_speed,
  max(speed)               AS max_speed
FROM telemetry
WHERE facility_id != ''
GROUP BY facility_id, hour;
