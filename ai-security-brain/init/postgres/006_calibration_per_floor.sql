-- ============================================================
-- 006: Allow per-floor calibrations
--
-- The original UNIQUE constraint on facility_id (from 004)
-- only allows one calibration per facility. Drop it so each
-- floor can have its own calibration row.
-- The per-floor unique index (idx_calibrations_floor_id from
-- 005) already prevents duplicate rows for the same floor.
-- ============================================================

-- Drop the facility-level unique constraint so multiple floors
-- in the same facility can each have their own calibration.
ALTER TABLE floor_plan_calibrations
    DROP CONSTRAINT IF EXISTS floor_plan_calibrations_facility_id_key;

-- Also handle the case where the constraint was auto-named differently
DO $$
BEGIN
    -- Try to find and drop any unique index on facility_id alone
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'floor_plan_calibrations'
          AND indexdef LIKE '%UNIQUE%facility_id%'
          AND indexdef NOT LIKE '%floor_id%'
    ) THEN
        -- The constraint name matches the column for single-column unique
        EXECUTE (
            SELECT 'DROP INDEX IF EXISTS ' || indexname
            FROM pg_indexes
            WHERE tablename = 'floor_plan_calibrations'
              AND indexdef LIKE '%UNIQUE%facility_id%'
              AND indexdef NOT LIKE '%floor_id%'
            LIMIT 1
        );
    END IF;
END $$;

-- Add a non-unique index on facility_id for lookup performance
CREATE INDEX IF NOT EXISTS idx_calibrations_facility_id
    ON floor_plan_calibrations(facility_id);
