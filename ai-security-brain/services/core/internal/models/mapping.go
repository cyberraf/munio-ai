package models

// MapUpdate is sent by a robot agent every ~2 seconds with occupancy grid data.
type MapUpdate struct {
	Type           string      `json:"type"`
	RobotID        string      `json:"robot_id"`
	GridDim        int         `json:"grid_dim"`
	ResolutionCm   int         `json:"resolution_cm"`
	GridSizeM      float64     `json:"grid_size_m"`
	RobotX         float64     `json:"robot_x"`
	RobotY         float64     `json:"robot_y"`
	RobotHeading   float64     `json:"robot_heading"`
	Trail          [][]float64 `json:"trail"`
	ObstaclePoints [][]float64 `json:"obstacle_points"`
	GridRLE        [][]int     `json:"grid_rle"`
	Timestamp      int64       `json:"timestamp_ms"`
}

// CameraSnapshot is a low-res JPEG frame from a robot's camera.
type CameraSnapshot struct {
	Type      string `json:"type"`
	RobotID   string `json:"robot_id"`
	Image     string `json:"image"`
	EdgeCount int    `json:"edge_count"`
	Timestamp int64  `json:"timestamp_ms"`
}

// RobotMapState holds the latest map contribution from a single robot.
type RobotMapState struct {
	RobotID        string      `json:"robot_id"`
	X              float64     `json:"x"`
	Y              float64     `json:"y"`
	Heading        float64     `json:"heading"`
	Trail          [][]float64 `json:"trail"`
	ObstaclePoints [][]float64 `json:"obstacle_points"`
	GridRLE        [][]int     `json:"grid_rle,omitempty"`
	GridDim        int         `json:"grid_dim,omitempty"`
	ResolutionCm   int         `json:"resolution_cm,omitempty"`
	GridSizeM      float64     `json:"grid_size_m,omitempty"`
	LatestSnapshot string      `json:"latest_snapshot,omitempty"`
	LastUpdate     int64       `json:"last_update"`
}

// FacilityMap holds the merged map state for all robots in a facility.
type FacilityMap struct {
	FacilityID string                   `json:"facility_id"`
	Robots     map[string]RobotMapState `json:"robots"`
	LastUpdate int64                    `json:"last_update"`
}
