-- Multi-robot support: add vendor/facility columns to incidents
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS robot_type TEXT DEFAULT '';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS vendor TEXT DEFAULT '';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS facility_id TEXT DEFAULT '';

-- Facilities table
CREATE TABLE IF NOT EXISTS facilities (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT,
  status      TEXT DEFAULT 'active',
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Robots registry (multi-vendor fleet)
CREATE TABLE IF NOT EXISTS robots_registry (
  robot_id     TEXT PRIMARY KEY,
  robot_type   TEXT NOT NULL,
  vendor       TEXT NOT NULL,
  facility_id  TEXT REFERENCES facilities(id),
  display_name TEXT,
  ip_address   TEXT,
  ssh_username TEXT,
  ssh_password TEXT,
  status       TEXT DEFAULT 'active',
  last_seen_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ DEFAULT now()
);

-- No seed data — robots and facilities are created via the dashboard.
