package models

import (
	"encoding/json"
	"time"
)

// Thresholds holds the configurable safety thresholds for the classifier.
type Thresholds struct {
	ProximityCm      float64 `json:"proximity_cm"`
	SpeedMax         float64 `json:"speed_max"`
	OffPathGrayscale int     `json:"off_path_grayscale"`
	LowBatteryV      float64 `json:"low_battery_v"`
}

// Metrics holds aggregated safety metrics for the dashboard.
type Metrics struct {
	TotalEvents        int64          `json:"total_events"`
	TotalIncidents     int64          `json:"total_incidents"`
	TotalEstops        int64          `json:"total_estops"`
	AvgSpeed           float64        `json:"avg_speed"`
	MinDistance         float64        `json:"min_distance"`
	HoursMonitored     float64        `json:"hours_monitored"`
	SpeedCompliancePct float64        `json:"speed_compliance_pct"`
	HourlyBreakdown    []HourlyMetric `json:"hourly_breakdown"`
}

type HourlyMetric struct {
	Hour             string  `json:"hour"` // ISO 8601 format
	EventCount       int64   `json:"event_count"`
	MinDistance       float64 `json:"min_distance"`
	AvgDistance       float64 `json:"avg_distance"`
	AvgSpeed         float64 `json:"avg_speed"`
	MaxSpeed         float64 `json:"max_speed"`
	ProximityAlerts  int64   `json:"proximity_alerts"`
	SpeedViolations  int64   `json:"speed_violations"`
	Estops           int64   `json:"estops"`
}

// SystemStatus represents the current system health.
type SystemStatus struct {
	RobotConnected bool    `json:"robot_connected"`
	UptimeSeconds  float64 `json:"uptime_seconds"`
	TotalEvents    int64   `json:"total_events"`
	DbStatus       string  `json:"db_status"`
}

// Command is sent from the dashboard to the robot.
type Command struct {
	Command string `json:"command"` // "estop" or "resume"
	RobotID string `json:"robot_id,omitempty"` // optional: target specific robot
}

// RobotPlatform describes a type/model of robot with its sensor capabilities.
type RobotPlatform struct {
	ID                string          `json:"id"`
	Name              string          `json:"name"`
	Manufacturer      string          `json:"manufacturer"`
	Category          string          `json:"category"`
	Sensors           json.RawMessage `json:"sensors"`
	DefaultThresholds json.RawMessage `json:"default_thresholds"`
	CreatedAt         time.Time       `json:"created_at"`
}

// Robot represents a registered robot instance linked to a platform.
type Robot struct {
	ID          string          `json:"id"`
	PlatformID  string          `json:"platform_id"`
	Name        string          `json:"name"`
	Description string          `json:"description"`
	AuthToken   string          `json:"auth_token"`
	IPAddress   string          `json:"ip_address"`
	MACAddress  string          `json:"mac_address"`
	Status      string          `json:"status"`
	Thresholds  json.RawMessage `json:"thresholds,omitempty"`
	CreatedAt   time.Time       `json:"created_at"`
	Platform    *RobotPlatform  `json:"platform,omitempty"`
}

// Facility represents a building where robots operate.
// Floor plans are stored on Floor, not directly on Facility.
type Facility struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	Status      string    `json:"status"`
	RobotCount  int       `json:"robot_count"`
	OnlineCount int       `json:"online_count"`
	Floors      []Floor   `json:"floors,omitempty"`
	// Deprecated — kept for backward compat; new code should use floors[].floor_plan_url
	FloorPlanURL    string `json:"floor_plan_url,omitempty"`
	FloorPlanWidth  int    `json:"floor_plan_width,omitempty"`
	FloorPlanHeight int    `json:"floor_plan_height,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
}

// Floor represents a single level within a facility (building).
// Each floor has its own floor plan image and calibration.
type Floor struct {
	ID              string    `json:"id"`
	FacilityID      string    `json:"facility_id"`
	FloorNumber     int       `json:"floor_number"`
	Name            string    `json:"name"`
	FloorPlanURL    string    `json:"floor_plan_url,omitempty"`
	FloorPlanWidth  int       `json:"floor_plan_width,omitempty"`
	FloorPlanHeight int       `json:"floor_plan_height,omitempty"`
	Status          string    `json:"status"`
	CreatedAt       time.Time `json:"created_at"`
}

// Room represents a labeled polygon region on a floor plan.
type Room struct {
	ID            string          `json:"id"`
	FloorID       string          `json:"floor_id"`
	Label         string          `json:"label"`
	RoomType      string          `json:"room_type"`
	PolygonPoints json.RawMessage `json:"polygon_points"`
	Color         string          `json:"color,omitempty"`
	CreatedAt     time.Time       `json:"created_at"`
}

// FloorPlanCalibration maps robot world coordinates to floor plan pixel positions.
type FloorPlanCalibration struct {
	ID             string    `json:"id,omitempty"`
	FacilityID     string    `json:"facility_id"`
	FloorID        string    `json:"floor_id,omitempty"`
	PixelsPerMeter float64   `json:"pixels_per_meter"`
	OriginPixelX   float64   `json:"origin_pixel_x"`
	OriginPixelY   float64   `json:"origin_pixel_y"`
	OriginWorldX   float64   `json:"origin_world_x"`
	OriginWorldY   float64   `json:"origin_world_y"`
	RotationRad    float64   `json:"rotation_rad"`
	ImageWidth     int       `json:"image_width"`
	ImageHeight    int       `json:"image_height"`
	CalibratedAt   time.Time `json:"calibrated_at,omitempty"`
}

// RobotInfo represents a robot in the multi-vendor fleet registry.
type RobotInfo struct {
	RobotID     string          `json:"robot_id"`
	RobotType   string          `json:"robot_type"`
	Vendor      string          `json:"vendor"`
	FacilityID  string          `json:"facility_id"`
	FloorID     string          `json:"floor_id,omitempty"`
	RoomIDs     json.RawMessage `json:"room_ids,omitempty"`
	DisplayName string          `json:"display_name"`
	IPAddress   string          `json:"ip_address,omitempty"`
	SSHUsername string          `json:"ssh_username,omitempty"`
	SSHPassword string          `json:"-"` // never sent to frontend
	Status      string          `json:"status"`
	Connected   bool            `json:"connected"`
	LastSeenAt  *time.Time      `json:"last_seen_at"`
}
