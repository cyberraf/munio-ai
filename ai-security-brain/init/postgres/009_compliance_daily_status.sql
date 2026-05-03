-- Migration 009: compliance_daily_status
-- Stores one row per facility per calendar day with 24-hour uptime metrics
-- and per-framework reportable incident counts produced by the compliance job.

CREATE TABLE IF NOT EXISTS compliance_daily_status (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    facility_id             TEXT        NOT NULL,
    checked_date            DATE        NOT NULL,

    -- Telemetry uptime for the 24-hour window ending at run time
    total_hours             FLOAT       NOT NULL DEFAULT 24.0,
    monitored_hours         FLOAT       NOT NULL DEFAULT 0.0,
    uptime_pct              FLOAT       NOT NULL DEFAULT 0.0,
    gap_count               INT         NOT NULL DEFAULT 0,
    max_gap_minutes         FLOAT       NOT NULL DEFAULT 0.0,

    -- Reportable incident counts within the same 24-hour window
    reportable_eu_ai_act    INT         NOT NULL DEFAULT 0,
    reportable_iso_10218    INT         NOT NULL DEFAULT 0,
    reportable_osha         INT         NOT NULL DEFAULT 0,
    reportable_ansi_r15_08  INT         NOT NULL DEFAULT 0,

    overall_status          TEXT        NOT NULL DEFAULT 'compliant',  -- compliant | attention_needed | non_compliant
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (facility_id, checked_date)
);

CREATE INDEX IF NOT EXISTS idx_cds_facility_date
    ON compliance_daily_status (facility_id, checked_date DESC);

CREATE INDEX IF NOT EXISTS idx_cds_status
    ON compliance_daily_status (overall_status, checked_date DESC)
    WHERE overall_status != 'compliant';
