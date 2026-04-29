-- Intelligence Engine schema additions

-- Extend incidents table with analysis fields
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS root_cause TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS root_cause_confidence FLOAT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS time_lost_seconds FLOAT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS is_hallucination BOOLEAN DEFAULT false;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS is_prevented BOOLEAN DEFAULT true;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS corroborated BOOLEAN;

-- Spatial hotspots
CREATE TABLE IF NOT EXISTS hotspots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  facility_id TEXT NOT NULL REFERENCES facilities(id),
  center_x FLOAT NOT NULL,
  center_y FLOAT NOT NULL,
  radius_m FLOAT NOT NULL,
  incident_count INT NOT NULL,
  dominant_type TEXT,
  dominant_cause TEXT,
  time_lost_hours FLOAT,
  description TEXT,
  recommendation TEXT,
  projected_savings_hours FLOAT,
  status TEXT DEFAULT 'active',
  first_seen TIMESTAMPTZ,
  last_incident TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Temporal patterns
CREATE TABLE IF NOT EXISTS temporal_patterns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  facility_id TEXT NOT NULL REFERENCES facilities(id),
  pattern_type TEXT NOT NULL,
  description TEXT NOT NULL,
  hour_of_day INT,
  day_of_week INT,
  incident_rate FLOAT,
  baseline_rate FLOAT,
  multiplier FLOAT,
  recommendation TEXT,
  detected_at TIMESTAMPTZ DEFAULT now()
);

-- Robot health scores
CREATE TABLE IF NOT EXISTS robot_health (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  robot_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  date DATE NOT NULL,
  incidents_per_hour FLOAT,
  sensor_reliability_pct FLOAT,
  battery_avg_voltage FLOAT,
  battery_trend FLOAT,
  behavioral_consistency FLOAT,
  hallucination_count INT,
  overall_score FLOAT,
  flags TEXT[],
  UNIQUE(robot_id, date)
);

-- Optimization recommendations
CREATE TABLE IF NOT EXISTS recommendations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  facility_id TEXT NOT NULL REFERENCES facilities(id),
  category TEXT NOT NULL,
  priority INT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  evidence JSONB,
  projected_time_saved_hours FLOAT,
  projected_cost_saved FLOAT,
  implementation_cost_est FLOAT,
  payback_days FLOAT,
  status TEXT DEFAULT 'open',
  implemented_at TIMESTAMPTZ,
  measured_impact JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Monthly reports
CREATE TABLE IF NOT EXISTS monthly_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  facility_id TEXT NOT NULL REFERENCES facilities(id),
  month DATE NOT NULL,
  total_incidents INT,
  total_time_lost_hours FLOAT,
  fleet_efficiency_pct FLOAT,
  hotspot_count INT,
  recommendations_count INT,
  report_url TEXT,
  generated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(facility_id, month)
);

-- Facility maps
CREATE TABLE IF NOT EXISTS facility_maps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  facility_id TEXT NOT NULL REFERENCES facilities(id),
  map_type TEXT NOT NULL,
  source TEXT,
  resolution FLOAT,
  origin_x FLOAT,
  origin_y FLOAT,
  width_pixels INT,
  height_pixels INT,
  s3_url TEXT NOT NULL,
  is_active BOOLEAN DEFAULT true,
  captured_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Map changes
CREATE TABLE IF NOT EXISTS map_changes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  facility_id TEXT NOT NULL,
  old_map_id UUID REFERENCES facility_maps(id),
  new_map_id UUID REFERENCES facility_maps(id),
  change_type TEXT,
  location_x FLOAT,
  location_y FLOAT,
  description TEXT,
  impact TEXT,
  detected_at TIMESTAMPTZ DEFAULT now()
);

-- Incident snapshots
CREATE TABLE IF NOT EXISTS incident_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id UUID REFERENCES incidents(id),
  robot_id TEXT NOT NULL,
  s3_url TEXT NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL,
  description TEXT
);
