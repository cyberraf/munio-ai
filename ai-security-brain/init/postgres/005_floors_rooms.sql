-- ============================================================
-- 005: Facility → Floor → Room hierarchy
--
-- Adds:
--   floors  — each facility (building) has one or more floors
--   rooms   — labeled polygon regions on a floor plan
--
-- Migrates existing data:
--   - Creates a default "Floor 1" for every existing facility
--   - Moves floor_plan_url/width/height from facilities to floors
--   - Backfills floor_id on calibrations/incidents/hotspots/maps
--
-- Backward compat:
--   - Does NOT drop floor_plan_* columns from facilities
--   - All new columns are nullable or have defaults
--   - Idempotent (IF NOT EXISTS / ON CONFLICT)
-- ============================================================

-- ─── 1. New tables ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS floors (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    facility_id       TEXT NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
    floor_number      INT NOT NULL DEFAULT 1,
    name              TEXT NOT NULL DEFAULT 'Floor 1',
    floor_plan_url    TEXT,
    floor_plan_width  INT,
    floor_plan_height INT,
    status            TEXT DEFAULT 'active',
    created_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE(facility_id, floor_number)
);

CREATE TABLE IF NOT EXISTS rooms (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    floor_id        UUID NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    room_type       TEXT DEFAULT 'general',
    polygon_points  JSONB NOT NULL DEFAULT '[]',
    color           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ─── 2. Add floor_id to coordinate-based tables ───────────

ALTER TABLE floor_plan_calibrations
    ADD COLUMN IF NOT EXISTS floor_id UUID REFERENCES floors(id);

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS floor_id UUID REFERENCES floors(id);

ALTER TABLE hotspots
    ADD COLUMN IF NOT EXISTS floor_id UUID REFERENCES floors(id);

ALTER TABLE facility_maps
    ADD COLUMN IF NOT EXISTS floor_id UUID REFERENCES floors(id);

ALTER TABLE map_changes
    ADD COLUMN IF NOT EXISTS floor_id UUID;

-- ─── 3. Migrate: create default Floor 1 for every facility ─

-- Facilities that have a floor plan → Floor 1 inherits the plan
INSERT INTO floors (facility_id, floor_number, name, floor_plan_url, floor_plan_width, floor_plan_height)
SELECT id, 1, 'Floor 1', floor_plan_url, floor_plan_width, floor_plan_height
FROM facilities
WHERE floor_plan_url IS NOT NULL
ON CONFLICT (facility_id, floor_number) DO NOTHING;

-- Facilities without a floor plan → still get a Floor 1 (for FK targets)
INSERT INTO floors (facility_id, floor_number, name)
SELECT id, 1, 'Floor 1'
FROM facilities
WHERE id NOT IN (SELECT DISTINCT facility_id FROM floors)
ON CONFLICT (facility_id, floor_number) DO NOTHING;

-- ─── 4. Backfill floor_id on existing rows ─────────────────

UPDATE floor_plan_calibrations SET floor_id = f.id
FROM floors f
WHERE floor_plan_calibrations.facility_id = f.facility_id
  AND f.floor_number = 1
  AND floor_plan_calibrations.floor_id IS NULL;

UPDATE incidents SET floor_id = f.id
FROM floors f
WHERE incidents.facility_id = f.facility_id
  AND f.floor_number = 1
  AND incidents.floor_id IS NULL
  AND incidents.facility_id IS NOT NULL
  AND incidents.facility_id != '';

UPDATE hotspots SET floor_id = f.id
FROM floors f
WHERE hotspots.facility_id = f.facility_id
  AND f.floor_number = 1
  AND hotspots.floor_id IS NULL;

UPDATE facility_maps SET floor_id = f.id
FROM floors f
WHERE facility_maps.facility_id = f.facility_id
  AND f.floor_number = 1
  AND facility_maps.floor_id IS NULL;

-- ─── 5. Index for calibration lookups by floor ─────────────

CREATE UNIQUE INDEX IF NOT EXISTS idx_calibrations_floor_id
    ON floor_plan_calibrations(floor_id)
    WHERE floor_id IS NOT NULL;
