package api

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/ai-security-brain/asb-core/internal/discovery"
	"github.com/ai-security-brain/asb-core/internal/models"
)

func handleListFacilities(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilities, err := deps.Postgres.GetFacilities(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query facilities")
			return
		}
		if facilities == nil {
			facilities = []models.Facility{}
		}

		// Enrich with online_count from IngestHandler
		connectedIDs := deps.Ingest.ConnectedRobotIDs()
		connectedSet := make(map[string]bool, len(connectedIDs))
		for _, id := range connectedIDs {
			connectedSet[id] = true
		}

		type facilityResponse struct {
			models.Facility
			OnlineCount int `json:"online_count"`
		}

		// For each facility, count connected robots from the registry
		allRobots, _ := deps.Postgres.GetRegistryRobots(r.Context(), "")
		facilityOnline := make(map[string]int)
		for _, robot := range allRobots {
			if connectedSet[robot.RobotID] {
				facilityOnline[robot.FacilityID]++
			}
		}

		results := make([]facilityResponse, len(facilities))
		for i, f := range facilities {
			floors, _ := deps.Postgres.GetFloors(r.Context(), f.ID)
			if floors == nil {
				floors = []models.Floor{}
			}
			f.Floors = floors
			results[i] = facilityResponse{
				Facility:    f,
				OnlineCount: facilityOnline[f.ID],
			}
		}
		writeJSON(w, http.StatusOK, results)
	}
}

func handleGetFacility(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")

		facilities, err := deps.Postgres.GetFacilities(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query facilities")
			return
		}

		var facility *models.Facility
		for i := range facilities {
			if facilities[i].ID == id {
				facility = &facilities[i]
				break
			}
		}
		if facility == nil {
			writeError(w, http.StatusNotFound, "facility not found")
			return
		}

		robots, err := deps.Postgres.GetRegistryRobots(r.Context(), id)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query robots")
			return
		}
		if robots == nil {
			robots = []models.RobotInfo{}
		}

		// Mark connected robots
		connectedIDs := deps.Ingest.ConnectedRobotIDs()
		connectedSet := make(map[string]bool, len(connectedIDs))
		for _, rid := range connectedIDs {
			connectedSet[rid] = true
		}
		for i := range robots {
			robots[i].Connected = connectedSet[robots[i].RobotID]
		}

		// Fetch floors for this facility
		floors, _ := deps.Postgres.GetFloors(r.Context(), id)
		if floors == nil {
			floors = []models.Floor{}
		}
		facility.Floors = floors

		writeJSON(w, http.StatusOK, map[string]any{
			"facility": facility,
			"robots":   robots,
			"floors":   floors,
		})
	}
}

func handleListAllRobots(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := r.URL.Query().Get("facility_id")

		robots, err := deps.Postgres.GetRegistryRobots(r.Context(), facilityID)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query robots")
			return
		}
		if robots == nil {
			robots = []models.RobotInfo{}
		}

		connectedIDs := deps.Ingest.ConnectedRobotIDs()
		connectedSet := make(map[string]bool, len(connectedIDs))
		for _, id := range connectedIDs {
			connectedSet[id] = true
		}
		for i := range robots {
			robots[i].Connected = connectedSet[robots[i].RobotID]
		}

		writeJSON(w, http.StatusOK, robots)
	}
}

func handleGetRobot(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")

		robots, err := deps.Postgres.GetRegistryRobots(r.Context(), "")
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query robots")
			return
		}

		var robot *models.RobotInfo
		for i := range robots {
			if robots[i].RobotID == id {
				robot = &robots[i]
				break
			}
		}
		if robot == nil {
			writeError(w, http.StatusNotFound, "robot not found")
			return
		}

		connectedIDs := deps.Ingest.ConnectedRobotIDs()
		for _, cid := range connectedIDs {
			if cid == robot.RobotID {
				robot.Connected = true
				break
			}
		}

		// Get latest telemetry
		latest := deps.GetLatestTelemetry(id)

		writeJSON(w, http.StatusOK, map[string]any{
			"robot":            robot,
			"latest_telemetry": latest,
		})
	}
}

func handleCreateFacility(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Name        string `json:"name"`
			Description string `json:"description"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Name == "" {
			writeError(w, http.StatusBadRequest, "name is required")
			return
		}
		id := strings.ToLower(strings.ReplaceAll(strings.TrimSpace(req.Name), " ", "-"))

		if err := deps.Postgres.CreateFacility(r.Context(), id, req.Name, req.Description); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to create facility")
			return
		}
		writeJSON(w, http.StatusCreated, map[string]string{"id": id, "name": req.Name})
	}
}

func handleUpdateFacility(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")
		var req struct {
			Name        string `json:"name"`
			Description string `json:"description"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		if req.Name == "" {
			writeError(w, http.StatusBadRequest, "name is required")
			return
		}
		if err := deps.Postgres.UpdateFacility(r.Context(), id, req.Name, req.Description); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to update facility")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "updated"})
	}
}

func handleDeleteFacility(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")
		if err := deps.Postgres.DeleteFacility(r.Context(), id); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to delete facility")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
	}
}

func handleListRegistryRobots(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := r.URL.Query().Get("facility_id")

		robots, err := deps.Postgres.GetRegistryRobots(r.Context(), facilityID)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query registry robots")
			return
		}
		if robots == nil {
			robots = []models.RobotInfo{}
		}
		writeJSON(w, http.StatusOK, robots)
	}
}

func handleStartRobot(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")

		robots, err := deps.Postgres.GetRegistryRobots(r.Context(), "")
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query robots")
			return
		}

		var robot *models.RobotInfo
		for i := range robots {
			if robots[i].RobotID == id {
				robot = &robots[i]
				break
			}
		}
		if robot == nil {
			writeError(w, http.StatusNotFound, "robot not found")
			return
		}
		if robot.IPAddress == "" || robot.SSHUsername == "" || robot.SSHPassword == "" {
			writeError(w, http.StatusBadRequest, "robot has no saved SSH credentials — redeploy the agent via Add Robot")
			return
		}

		log.Printf("[robot] starting agent on %s@%s (robot_id=%s)", robot.SSHUsername, robot.IPAddress, id)
		if err := discovery.RestartAgent(robot.IPAddress, robot.SSHUsername, robot.SSHPassword); err != nil {
			log.Printf("[robot] start failed: %v", err)
			writeError(w, http.StatusInternalServerError, "failed to start agent: "+err.Error())
			return
		}

		writeJSON(w, http.StatusOK, map[string]string{"status": "starting", "robot_id": id})
	}
}
