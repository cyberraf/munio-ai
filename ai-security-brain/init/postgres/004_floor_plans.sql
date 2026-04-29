-- Floor plan support for facilities
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS floor_plan_url TEXT;
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS floor_plan_width INT;
ALTER TABLE facilities ADD COLUMN IF NOT EXISTS floor_plan_height INT;

-- Floor plan calibration (maps robot coordinates to floor plan pixels)
CREATE TABLE IF NOT EXISTS floor_plan_calibrations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    facility_id     TEXT NOT NULL REFERENCES facilities(id) UNIQUE,
    pixels_per_meter FLOAT NOT NULL,
    origin_pixel_x  FLOAT NOT NULL,
    origin_pixel_y  FLOAT NOT NULL,
    origin_world_x  FLOAT DEFAULT 0,
    origin_world_y  FLOAT DEFAULT 0,
    rotation_rad    FLOAT DEFAULT 0,
    image_width     INT,
    image_height    INT,
    calibrated_at   TIMESTAMPTZ DEFAULT now()
);
