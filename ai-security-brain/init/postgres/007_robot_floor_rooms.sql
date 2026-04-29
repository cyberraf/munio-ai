-- ============================================================
-- 007: Robot floor & room assignments
--
-- Each robot can be assigned to a specific floor within its
-- facility, and to one or more rooms on that floor.
-- ============================================================

ALTER TABLE robots_registry
    ADD COLUMN IF NOT EXISTS floor_id UUID REFERENCES floors(id) ON DELETE SET NULL;

ALTER TABLE robots_registry
    ADD COLUMN IF NOT EXISTS room_ids JSONB DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_robots_registry_floor_id
    ON robots_registry(floor_id);
