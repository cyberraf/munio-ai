package models

import "time"

// ClassifiedEvent is produced by the rule-based classifier when a safety event is detected.
type ClassifiedEvent struct {
	ID            string    `json:"id"`
	RobotID       string    `json:"robot_id"`
	EventType     string    `json:"event_type"`
	Severity      string    `json:"severity"`
	Description   string    `json:"description"`
	DistanceCm    float64   `json:"distance_cm"`
	Speed         float64   `json:"speed"`
	SteeringAngle float64   `json:"steering_angle"`
	OccurredAt    time.Time `json:"occurred_at"`
	CreatedAt     time.Time `json:"created_at"`
}

// Event type constants
const (
	EventProximityAlert = "PROXIMITY_ALERT"
	EventSpeedViolation = "SPEED_VIOLATION"
	EventEstopTriggered = "ESTOP_TRIGGERED"
	EventPathDeviation  = "PATH_DEVIATION"
	EventSensorFailure  = "SENSOR_FAILURE"
	EventRobotFault     = "ROBOT_FAULT"
)

// Severity constants
const (
	SeverityCritical = "CRITICAL"
	SeverityHigh     = "HIGH"
	SeverityMedium   = "MEDIUM"
	SeverityLow      = "LOW"
)

// IncidentDetail includes the incident plus surrounding telemetry context.
type IncidentDetail struct {
	Incident         ClassifiedEvent  `json:"incident"`
	TelemetryContext []TelemetryEvent `json:"telemetry_context"` // ±5 seconds of telemetry
}
