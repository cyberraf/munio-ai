package models

import "time"

// TelemetryEvent is received from the PiCar-X robot via WebSocket at 10 Hz.
type TelemetryEvent struct {
	RobotID        string    `json:"robot_id"`
	TimestampMs    int64     `json:"timestamp_ms"`
	DistanceCm     float64   `json:"distance_cm"`
	Speed          float64   `json:"speed"`
	SteeringAngle  float64   `json:"steering_angle"`
	CameraPan      float64   `json:"camera_pan"`
	CameraTilt     float64   `json:"camera_tilt"`
	Grayscale      Grayscale `json:"grayscale"`
	BatteryVoltage float64   `json:"battery_voltage"`
	Status         string    `json:"status"` // "active", "idle", "estop"
	RobotType      string    `json:"robot_type"`
	Vendor         string    `json:"vendor"`
	FacilityID     string    `json:"facility_id"`
	PositionX      float64   `json:"position_x"`
	PositionY      float64   `json:"position_y"`
	HeadingDeg     float64   `json:"heading_deg"`
}

type Grayscale struct {
	Left   int `json:"left"`
	Center int `json:"center"`
	Right  int `json:"right"`
}

// Timestamp returns the event time as time.Time (UTC).
func (t *TelemetryEvent) Timestamp() time.Time {
	return time.UnixMilli(t.TimestampMs).UTC()
}
