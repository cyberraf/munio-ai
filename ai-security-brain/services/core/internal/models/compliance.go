package models

// ComplianceMapping links a safety event to the relevant regulatory standards
// and determines reportability obligations under each framework.
type ComplianceMapping struct {
	SafetyStandardRef      string `json:"safety_standard_ref"`
	SafetyClause           string `json:"safety_clause"`
	RegulatoryFramework    string `json:"regulatory_framework"`
	ComplianceCategory     string `json:"compliance_category"`
	ReportableUnderEUAIAct bool   `json:"reportable_under_eu_ai_act"`
	ReportableUnderOSHA    bool   `json:"reportable_under_osha"`
	MonitoringRequirement  string `json:"monitoring_requirement"`
}

// Regulatory framework identifiers.
const (
	FrameworkISO10218  = "ISO_10218"
	FrameworkOSHA1910  = "OSHA_1910"
	FrameworkEUAIAct   = "EU_AI_ACT"
	FrameworkANSIR1508 = "ANSI_R15_08"
	FrameworkIEC62443  = "IEC_62443"
)

// Compliance category identifiers.
const (
	CategoryProximitySafety  = "proximity_safety"
	CategorySpeedControl     = "speed_control"
	CategoryEmergencyStop    = "emergency_stop"
	CategorySensorIntegrity  = "sensor_integrity"
	CategoryPathCompliance   = "path_compliance"
)

// Monitoring requirement identifiers.
const (
	MonitoringContinuous     = "continuous"
	MonitoringPeriodic       = "periodic"
	MonitoringEventTriggered = "event_triggered"
)

// MapEventToCompliance returns the compliance mapping for the given event type
// and severity combination. Reportability thresholds vary per standard.
func MapEventToCompliance(eventType string, severity string) ComplianceMapping {
	isCritical := severity == SeverityCritical
	isHighOrAbove := severity == SeverityCritical || severity == SeverityHigh

	switch eventType {
	case EventProximityAlert:
		return ComplianceMapping{
			SafetyStandardRef:      "ISO 10218-2:2025 §5.10.3 / ANSI R15.08 §6.3.2 / OSHA 1910.212",
			SafetyClause:           "Zone breach - restricted area minimum separation distance violated",
			RegulatoryFramework:    FrameworkISO10218,
			ComplianceCategory:     CategoryProximitySafety,
			ReportableUnderEUAIAct: isHighOrAbove,
			ReportableUnderOSHA:    isCritical,
			MonitoringRequirement:  MonitoringContinuous,
		}

	case EventSpeedViolation:
		return ComplianceMapping{
			SafetyStandardRef:      "ISO 10218-2:2025 §5.4 / ANSI R15.08 §6.2.1",
			SafetyClause:           "Speed limitation - robot exceeded permitted velocity for operating zone",
			RegulatoryFramework:    FrameworkISO10218,
			ComplianceCategory:     CategorySpeedControl,
			ReportableUnderEUAIAct: severity == SeverityHigh,
			ReportableUnderOSHA:    false,
			MonitoringRequirement:  MonitoringContinuous,
		}

	case EventEstopTriggered:
		return ComplianceMapping{
			SafetyStandardRef:      "ISO 10218-1:2025 §5.5.2 / ANSI R15.08 §5.3.4 / OSHA 1910.212(a)(3)",
			SafetyClause:           "Emergency stop activation - safety-rated monitored stop engaged",
			RegulatoryFramework:    FrameworkISO10218,
			ComplianceCategory:     CategoryEmergencyStop,
			ReportableUnderEUAIAct: true,
			ReportableUnderOSHA:    true,
			MonitoringRequirement:  MonitoringEventTriggered,
		}

	case EventPathDeviation:
		return ComplianceMapping{
			SafetyStandardRef:      "ISO 10218-2:2025 §5.12 / ANSI R15.08 §6.4",
			SafetyClause:           "Safeguarded space violation - robot deviated outside designated operating path",
			RegulatoryFramework:    FrameworkISO10218,
			ComplianceCategory:     CategoryPathCompliance,
			ReportableUnderEUAIAct: true,
			ReportableUnderOSHA:    false,
			MonitoringRequirement:  MonitoringContinuous,
		}

	case EventSensorFailure:
		return ComplianceMapping{
			SafetyStandardRef:      "ISO 10218-1:2025 §5.4 / IEC 62443-3-3 SR 3.5",
			SafetyClause:           "Control system safety - protective sensor degraded or offline",
			RegulatoryFramework:    FrameworkISO10218,
			ComplianceCategory:     CategorySensorIntegrity,
			ReportableUnderEUAIAct: severity == SeverityHigh,
			ReportableUnderOSHA:    isCritical,
			MonitoringRequirement:  MonitoringPeriodic,
		}

	default:
		return ComplianceMapping{
			SafetyStandardRef:      "ISO 10218-1:2025 §5.1",
			SafetyClause:           "General safety requirement",
			RegulatoryFramework:    FrameworkISO10218,
			ComplianceCategory:     "general",
			ReportableUnderEUAIAct: false,
			ReportableUnderOSHA:    false,
			MonitoringRequirement:  MonitoringPeriodic,
		}
	}
}

// ComplianceRequirement is a row from the compliance_requirements reference table.
type ComplianceRequirement struct {
	ID                   string   `json:"id"`
	Framework            string   `json:"framework"`
	RequirementID        string   `json:"requirement_id"`
	Title                string   `json:"title"`
	Description          string   `json:"description"`
	MonitoringType       string   `json:"monitoring_type"`
	ApplicableEventTypes []string `json:"applicable_event_types"`
	SeverityThreshold    string   `json:"severity_threshold"`
	ReportingDeadline    string   `json:"reporting_deadline"`
	Active               bool     `json:"active"`
}

// MonitoringSpec describes what a regulatory framework requires for ongoing
// robot safety monitoring.
type MonitoringSpec struct {
	Framework   string `json:"framework"`
	Requirement string `json:"requirement"`
	Description string `json:"description"`
}

// GetMonitoringRequirements returns each regulatory framework's monitoring
// obligations as they apply to industrial robot deployments.
func GetMonitoringRequirements() map[string]MonitoringSpec {
	return map[string]MonitoringSpec{
		FrameworkISO10218: {
			Framework:   FrameworkISO10218,
			Requirement: MonitoringContinuous,
			Description: "ISO 10218-1/2:2025 requires continuous real-time monitoring of speed, separation distance, and safeguarded space boundaries during all robot operation modes. Periodic risk assessments must be documented after any configuration change.",
		},
		FrameworkOSHA1910: {
			Framework:   FrameworkOSHA1910,
			Requirement: MonitoringPeriodic,
			Description: "OSHA 1910.212 requires periodic inspection of machine guarding and safety devices. All emergency stop activations and proximity incidents resulting in injury or near-miss must be logged and reported within the required timeframe.",
		},
		FrameworkEUAIAct: {
			Framework:   FrameworkEUAIAct,
			Requirement: MonitoringContinuous,
			Description: "EU AI Act (Regulation 2024/1689) classifies autonomous mobile robots in shared human workspaces as high-risk AI systems. Continuous logging of AI-driven decisions, post-market monitoring, and incident reporting to the national authority within 15 days of a serious incident are mandatory.",
		},
		FrameworkANSIR1508: {
			Framework:   FrameworkANSIR1508,
			Requirement: MonitoringContinuous,
			Description: "ANSI/RIA R15.08 requires continuous monitoring of AMR speed, path adherence, and zone compliance. Operators must maintain audit logs of all safety-related events and conduct formal incident investigations for CRITICAL-severity events.",
		},
		FrameworkIEC62443: {
			Framework:   FrameworkIEC62443,
			Requirement: MonitoringEventTriggered,
			Description: "IEC 62443-3-3 SR 3.5 requires event-triggered monitoring of sensor integrity and control system health. Anomalies in safety-critical sensor data streams must be flagged and reviewed within one operational cycle.",
		},
	}
}
