-- ============================================================
-- 008: Compliance tracking
--
-- Adds regulatory compliance metadata to incidents, a table
-- for generated compliance reports, and a seeded reference
-- table of requirements per framework (EU AI Act, ISO 10218,
-- OSHA, ANSI R15.08, IEC 62443).
-- ============================================================

-- ─── Compliance columns on incidents ─────────────────────────────────────────

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS safety_standard_ref    TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS safety_clause          TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS regulatory_framework   TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS compliance_category    TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reportable_eu_ai_act   BOOLEAN DEFAULT false;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reportable_osha        BOOLEAN DEFAULT false;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS monitoring_requirement TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS tags                   TEXT[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_incidents_regulatory_framework
    ON incidents(regulatory_framework, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_incidents_reportable_eu
    ON incidents(reportable_eu_ai_act, occurred_at DESC)
    WHERE reportable_eu_ai_act = true;

CREATE INDEX IF NOT EXISTS idx_incidents_reportable_osha
    ON incidents(reportable_osha, occurred_at DESC)
    WHERE reportable_osha = true;

CREATE INDEX IF NOT EXISTS idx_incidents_tags
    ON incidents USING GIN(tags);

-- ─── compliance_reports ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS compliance_reports (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    facility_id         TEXT        NOT NULL REFERENCES facilities(id),
    report_type         TEXT        NOT NULL,   -- 'EU_AI_ACT' | 'ISO_10218' | 'OSHA' | 'ANSI_R15_08' | 'INTERNAL'
    period_start        DATE        NOT NULL,
    period_end          DATE        NOT NULL,
    total_incidents     INT,
    reportable_incidents INT,
    compliance_score    FLOAT,                  -- 0–100
    findings            JSONB,
    recommendations     JSONB,
    report_url          TEXT,
    generated_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (facility_id, report_type, period_start)
);

CREATE INDEX IF NOT EXISTS idx_compliance_reports_facility
    ON compliance_reports(facility_id, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_compliance_reports_type
    ON compliance_reports(report_type, period_start DESC);

-- ─── compliance_requirements ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS compliance_requirements (
    id                    UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    framework             TEXT    NOT NULL,   -- EU_AI_ACT | ISO_10218 | OSHA | ANSI_R15_08 | IEC_62443
    requirement_id        TEXT    NOT NULL,   -- e.g. "Art.9.1", "§5.10.3"
    title                 TEXT    NOT NULL,
    description           TEXT,
    monitoring_type       TEXT,               -- continuous | periodic | event_triggered
    applicable_event_types TEXT[],
    severity_threshold    TEXT,
    reporting_deadline    TEXT,
    active                BOOLEAN DEFAULT true,
    UNIQUE (framework, requirement_id)
);

CREATE INDEX IF NOT EXISTS idx_compliance_req_framework
    ON compliance_requirements(framework)
    WHERE active = true;

CREATE INDEX IF NOT EXISTS idx_compliance_req_event_types
    ON compliance_requirements USING GIN(applicable_event_types);

-- ─── Seed: EU AI Act ──────────────────────────────────────────────────────────

INSERT INTO compliance_requirements
    (framework, requirement_id, title, description, monitoring_type, applicable_event_types, severity_threshold, reporting_deadline)
VALUES
    (
        'EU_AI_ACT', 'Art.9.1',
        'Risk management system',
        'High-risk AI systems shall have a risk management system covering the entire lifecycle. '
        'Autonomous mobile robots operating in shared human workspaces are classified as high-risk '
        'under Annex III. Risk management must include identification and analysis of known and '
        'foreseeable risks, estimation and evaluation of risks, and adoption of risk management measures.',
        'continuous',
        ARRAY['PROXIMITY_ALERT', 'ESTOP_TRIGGERED', 'PATH_DEVIATION', 'SPEED_VIOLATION', 'SENSOR_FAILURE'],
        'MEDIUM',
        NULL
    ),
    (
        'EU_AI_ACT', 'Art.12.1',
        'Record-keeping and logging',
        'High-risk AI systems shall be designed and developed with capabilities enabling automatic '
        'recording of events (logs) throughout their lifetime. Logging facilities shall enable '
        'monitoring of the operation of the AI system with respect to the occurrence of situations '
        'that may result in risks. Logs must be retained for a minimum period appropriate to the '
        'intended purpose of the system.',
        'continuous',
        ARRAY['PROXIMITY_ALERT', 'ESTOP_TRIGGERED', 'PATH_DEVIATION', 'SPEED_VIOLATION', 'SENSOR_FAILURE'],
        'LOW',
        NULL
    ),
    (
        'EU_AI_ACT', 'Art.62',
        'Serious incident reporting',
        'Providers of high-risk AI systems shall report serious incidents to the market surveillance '
        'authority of the Member State where the incident occurred. A serious incident means any '
        'incident or malfunction that directly or indirectly leads to death, serious injury, significant '
        'property damage, or a breach of fundamental rights obligations. Report must be submitted '
        'without undue delay and no later than 15 days after the provider becomes aware.',
        'event_triggered',
        ARRAY['ESTOP_TRIGGERED', 'PROXIMITY_ALERT', 'SENSOR_FAILURE'],
        'CRITICAL',
        '15 days'
    ),
    (
        'EU_AI_ACT', 'Art.72',
        'Post-market monitoring',
        'Providers of high-risk AI systems shall establish and document a post-market monitoring system '
        'proportionate to the nature of the AI technologies and the risks. The system shall actively '
        'and systematically collect, document and analyse data on performance of high-risk AI systems '
        'throughout their lifetime, including through feedback loops from deployers.',
        'periodic',
        ARRAY['PROXIMITY_ALERT', 'ESTOP_TRIGGERED', 'PATH_DEVIATION', 'SPEED_VIOLATION', 'SENSOR_FAILURE'],
        'LOW',
        '30 days'
    )
ON CONFLICT (framework, requirement_id) DO NOTHING;

-- ─── Seed: ISO 10218-2:2025 ───────────────────────────────────────────────────

INSERT INTO compliance_requirements
    (framework, requirement_id, title, description, monitoring_type, applicable_event_types, severity_threshold, reporting_deadline)
VALUES
    (
        'ISO_10218', '§5.4',
        'Speed and force limitation',
        'The robot system shall limit speed and applied forces/torques to safe levels. Speed '
        'monitoring shall be continuously active during collaborative operation. The maximum speed '
        'for the robot tool centre point (TCP) and any attached workpiece shall not exceed limits '
        'defined by the risk assessment. Speed violations shall trigger an immediate protective stop.',
        'continuous',
        ARRAY['SPEED_VIOLATION', 'ESTOP_TRIGGERED'],
        'MEDIUM',
        NULL
    ),
    (
        'ISO_10218', '§5.5',
        'Emergency stop functions',
        'Each robot system shall be equipped with emergency stop function(s) conforming to '
        'IEC 60204-1:2016 and ISO 13850. Emergency stop devices shall be provided at each operator '
        'control position and wherever required by the risk assessment. Every emergency stop activation '
        'shall be logged with timestamp, robot state, and triggering condition. Devices shall be '
        'manually resettable only.',
        'event_triggered',
        ARRAY['ESTOP_TRIGGERED'],
        'CRITICAL',
        NULL
    ),
    (
        'ISO_10218', '§5.10',
        'Minimum distance — separation distance',
        'A minimum separation distance shall be established and enforced between the robot system '
        'and human operators based on the stopping performance of the robot. The separation distance '
        'shall account for robot speed, reaction time, and human approach speed. The system shall '
        'continuously monitor this distance and initiate a protective stop when the minimum separation '
        'distance is breached. Re-start shall only be permitted after verification that the hazardous '
        'zone is clear.',
        'continuous',
        ARRAY['PROXIMITY_ALERT', 'ESTOP_TRIGGERED'],
        'HIGH',
        NULL
    ),
    (
        'ISO_10218', '§5.12',
        'Safeguarded space and path compliance',
        'The safeguarded space shall be defined and enforced for all operating modes. The robot '
        'shall not deviate from its designated path into the safeguarded human-occupied zones without '
        'triggering a protective stop. Path boundaries shall be continuously monitored. Any deviation '
        'outside the safeguarded space constitutes a safety function failure and shall be logged, '
        'investigated, and resolved before resuming operation.',
        'continuous',
        ARRAY['PATH_DEVIATION', 'ESTOP_TRIGGERED'],
        'LOW',
        NULL
    )
ON CONFLICT (framework, requirement_id) DO NOTHING;

-- ─── Seed: OSHA ───────────────────────────────────────────────────────────────

INSERT INTO compliance_requirements
    (framework, requirement_id, title, description, monitoring_type, applicable_event_types, severity_threshold, reporting_deadline)
VALUES
    (
        'OSHA', '1910.212',
        'General requirements — machine guarding',
        'One or more methods of machine guarding shall be provided to protect operators and other '
        'employees in the machine area from hazards such as those created by point of operation, '
        'ingoing nip points, rotating parts, flying chips and sparks. For autonomous mobile robots, '
        'this includes virtual safety zones monitored by proximity sensors. All emergency stop '
        'activations and proximity incidents that result in a near-miss or injury must be logged and '
        'reported to OSHA within the applicable timeframe.',
        'periodic',
        ARRAY['PROXIMITY_ALERT', 'ESTOP_TRIGGERED', 'SENSOR_FAILURE'],
        'CRITICAL',
        '8 hours'
    ),
    (
        'OSHA', '1910.147',
        'Control of hazardous energy (lockout/tagout)',
        'The standard covers the servicing and maintenance of machines and equipment in which the '
        'unexpected energization or startup of the machines or equipment, or release of stored energy, '
        'could harm employees. For robot systems, this requires documented energy control procedures, '
        'authorized employee training, and a periodic inspection of the energy control procedure. '
        'Any robot entering an unintended state (e-stop, fault) during maintenance must be fully '
        'de-energized and locked out before personnel enter the hazard zone.',
        'event_triggered',
        ARRAY['ESTOP_TRIGGERED', 'SENSOR_FAILURE', 'ROBOT_FAULT'],
        'HIGH',
        '72 hours'
    )
ON CONFLICT (framework, requirement_id) DO NOTHING;

-- ─── Seed: ANSI R15.08 ────────────────────────────────────────────────────────

INSERT INTO compliance_requirements
    (framework, requirement_id, title, description, monitoring_type, applicable_event_types, severity_threshold, reporting_deadline)
VALUES
    (
        'ANSI_R15_08', '§5.3',
        'Safety requirements — risk assessment and mitigation',
        'AMR systems shall undergo a risk assessment conforming to ISO 12100 before deployment. '
        'Risk assessment shall consider all robot operating modes, all reasonably foreseeable '
        'interactions with humans, and the full operational environment. Identified risks shall be '
        'mitigated through design, safeguarding, or administrative controls. The risk assessment '
        'shall be documented, reviewed after any change to the system, and retained for the lifetime '
        'of the system.',
        'periodic',
        ARRAY['PROXIMITY_ALERT', 'ESTOP_TRIGGERED', 'PATH_DEVIATION', 'SPEED_VIOLATION', 'SENSOR_FAILURE'],
        'MEDIUM',
        NULL
    ),
    (
        'ANSI_R15_08', '§6.2',
        'Speed monitoring and limiting',
        'The AMR shall continuously monitor its speed and enforce the maximum permitted speed for '
        'each operating zone. Speed limits shall be defined per zone in the site risk assessment. '
        'The AMR shall automatically reduce speed when entering zones with increased risk or '
        'reduced separation distance. All speed violations shall be logged with robot ID, zone, '
        'measured speed, permitted speed, and timestamp. Audit logs must be retained for a minimum '
        'of 12 months and made available to regulators on request.',
        'continuous',
        ARRAY['SPEED_VIOLATION'],
        'MEDIUM',
        NULL
    ),
    (
        'ANSI_R15_08', '§6.3',
        'Proximity detection and separation distance',
        'The AMR shall be equipped with sensors capable of detecting humans and obstacles within '
        'the separation distance defined by the risk assessment. Detection shall trigger a speed '
        'reduction or protective stop proportional to the detected distance and relative velocity. '
        'Proximity events shall be logged with sensor readings, robot state, and response taken. '
        'Operators must conduct formal incident investigation for any CRITICAL proximity event '
        'within 24 hours.',
        'continuous',
        ARRAY['PROXIMITY_ALERT'],
        'HIGH',
        '24 hours'
    ),
    (
        'ANSI_R15_08', '§6.4',
        'Navigation and path adherence',
        'The AMR navigation system shall maintain the robot within its designated operating path '
        'or zone. Path deviations outside the defined operational design domain (ODD) shall be '
        'treated as safety function failures. The system shall log all path deviations with '
        'position data, deviation magnitude, and corrective action. Repeated path deviations '
        'on the same route shall trigger a mandatory maintenance review.',
        'continuous',
        ARRAY['PATH_DEVIATION'],
        'LOW',
        NULL
    )
ON CONFLICT (framework, requirement_id) DO NOTHING;
