package api

import (
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/ai-security-brain/asb-core/internal/models"
)

const activationPort = "8765"

func handleListPlatforms(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		platforms, err := deps.Postgres.GetPlatforms(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query platforms")
			return
		}
		if platforms == nil {
			platforms = []models.RobotPlatform{}
		}
		writeJSON(w, http.StatusOK, platforms)
	}
}

func handleCreateRobot(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var robot models.Robot
		if err := json.NewDecoder(r.Body).Decode(&robot); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		if robot.Name == "" {
			writeError(w, http.StatusBadRequest, "name is required")
			return
		}
		if robot.PlatformID == "" {
			writeError(w, http.StatusBadRequest, "platform_id is required")
			return
		}

		platform, err := deps.Postgres.GetPlatform(r.Context(), robot.PlatformID)
		if err != nil || platform == nil {
			writeError(w, http.StatusBadRequest, "invalid platform_id")
			return
		}

		created, err := deps.Postgres.CreateRobot(r.Context(), robot)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to create robot (name may already exist)")
			return
		}
		created.Platform = platform
		writeJSON(w, http.StatusCreated, created)
	}
}

func handleDeleteRobot(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")
		if err := deps.Postgres.DeleteRobot(r.Context(), id); err != nil {
			writeError(w, http.StatusNotFound, "robot not found")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
	}
}

// handleActivateRobot sends an activation request to the robot's activation_server.py
// running at robot.ip_address:8765. The robot must have an IP address configured.
func handleActivateRobot(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")

		robot, err := deps.Postgres.GetRobot(r.Context(), id)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query robot")
			return
		}
		if robot == nil {
			writeError(w, http.StatusNotFound, "robot not found")
			return
		}
		if robot.IPAddress == "" {
			writeError(w, http.StatusBadRequest, "robot has no IP address — set it in Settings first")
			return
		}

		url := fmt.Sprintf("http://%s:%s/activate", robot.IPAddress, activationPort)
		req, err := http.NewRequestWithContext(r.Context(), "POST", url, nil)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to build activation request")
			return
		}
		req.Header.Set("X-Auth-Token", robot.AuthToken)

		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			writeError(w, http.StatusBadGateway,
				fmt.Sprintf("cannot reach robot at %s — is the activation server running?", robot.IPAddress))
			return
		}
		defer resp.Body.Close()

		switch resp.StatusCode {
		case http.StatusOK:
			writeJSON(w, http.StatusOK, map[string]string{
				"status": "activating",
				"robot":  robot.Name,
				"ip":     robot.IPAddress,
			})
		case http.StatusUnauthorized:
			writeError(w, http.StatusUnauthorized, "robot rejected the auth token")
		default:
			writeError(w, http.StatusBadGateway, "robot returned unexpected status")
		}
	}
}

// handleDeactivateRobot sends a deactivation request to stop the robot's agent.
func handleDeactivateRobot(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")

		robot, err := deps.Postgres.GetRobot(r.Context(), id)
		if err != nil || robot == nil {
			writeError(w, http.StatusNotFound, "robot not found")
			return
		}
		if robot.IPAddress == "" {
			writeError(w, http.StatusBadRequest, "robot has no IP address configured")
			return
		}

		url := fmt.Sprintf("http://%s:%s/deactivate", robot.IPAddress, activationPort)
		req, err := http.NewRequestWithContext(r.Context(), "POST", url, nil)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to build request")
			return
		}
		req.Header.Set("X-Auth-Token", robot.AuthToken)

		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			writeError(w, http.StatusBadGateway,
				fmt.Sprintf("cannot reach robot at %s", robot.IPAddress))
			return
		}
		defer resp.Body.Close()
		writeJSON(w, http.StatusOK, map[string]string{"status": "deactivating", "robot": robot.Name})
	}
}

// handleUpdateRobotAssignment updates a robot's facility, floor, and room assignments.
func handleUpdateRobotAssignment(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		robotID := chi.URLParam(r, "id")
		var body struct {
			FacilityID string   `json:"facility_id"`
			FloorID    string   `json:"floor_id"`
			RoomIDs    []string `json:"room_ids"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON")
			return
		}
		roomJSON, err := json.Marshal(body.RoomIDs)
		if err != nil {
			roomJSON = []byte("[]")
		}
		if err := deps.Postgres.UpdateRobotAssignment(r.Context(), robotID, body.FacilityID, body.FloorID, roomJSON); err != nil {
			log.Printf("[robots] assignment update error for %s: %v", robotID, err)
			writeError(w, http.StatusInternalServerError, "failed to update assignment")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	}
}

// handleGetRobotBoundaries returns the robot's assigned rooms as world-coordinate
// polygons, plus calibration data, so mock agents can constrain their movement.
func handleGetRobotBoundaries(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		robotID := chi.URLParam(r, "id")

		// Look up robot in registry
		allRobots, err := deps.Postgres.GetRegistryRobots(r.Context(), "")
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query robots")
			return
		}
		var robot *models.RobotInfo
		for _, rb := range allRobots {
			if rb.RobotID == robotID {
				robot = &rb
				break
			}
		}
		if robot == nil {
			writeError(w, http.StatusNotFound, "robot not found")
			return
		}
		if robot.FloorID == "" {
			writeError(w, http.StatusNotFound, "robot has no floor assignment")
			return
		}

		// Get calibration for this floor
		cal, err := deps.Postgres.GetCalibrationByFloor(r.Context(), robot.FloorID)
		if err != nil || cal == nil {
			writeError(w, http.StatusNotFound, "no calibration for assigned floor")
			return
		}

		// Get assigned room IDs
		var roomIDs []string
		if robot.RoomIDs != nil {
			json.Unmarshal(robot.RoomIDs, &roomIDs)
		}

		// Get all rooms for this floor, filter to assigned ones
		allRooms, _ := deps.Postgres.GetRooms(r.Context(), robot.FloorID)
		assignedSet := make(map[string]bool)
		for _, id := range roomIDs {
			assignedSet[id] = true
		}

		// Convert room polygons from image pixels to world coordinates
		type WorldPoint struct {
			X float64 `json:"x"`
			Y float64 `json:"y"`
		}
		type WorldRoom struct {
			ID       string       `json:"id"`
			Label    string       `json:"label"`
			Polygon  []WorldPoint `json:"polygon"`
		}

		ppm := cal.PixelsPerMeter
		rot := cal.RotationRad
		cosR := math.Cos(rot)
		sinR := math.Sin(rot)

		var worldRooms []WorldRoom
		for _, room := range allRooms {
			if len(roomIDs) > 0 && !assignedSet[room.ID] {
				continue
			}
			// Parse polygon points
			var pixels []struct{ X, Y float64 }
			json.Unmarshal(room.PolygonPoints, &pixels)
			if len(pixels) < 3 {
				continue
			}

			var worldPts []WorldPoint
			for _, p := range pixels {
				// Reverse the worldToImage transform:
				// px = origin_px + (dx*cos - dy*sin) * ppm
				// py = origin_py - (dx*sin + dy*cos) * ppm
				// Solving for dx, dy (world offset from origin):
				rpx := (p.X - cal.OriginPixelX) / ppm
				rpy := (cal.OriginPixelY - p.Y) / ppm
				// Inverse rotation
				wx := rpx*cosR + rpy*sinR + cal.OriginWorldX
				wy := -rpx*sinR + rpy*cosR + cal.OriginWorldY
				worldPts = append(worldPts, WorldPoint{X: wx, Y: wy})
			}
			worldRooms = append(worldRooms, WorldRoom{
				ID:      room.ID,
				Label:   room.Label,
				Polygon: worldPts,
			})
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"robot_id":    robotID,
			"facility_id": robot.FacilityID,
			"floor_id":    robot.FloorID,
			"calibration": cal,
			"rooms":       worldRooms,
		})
	}
}
