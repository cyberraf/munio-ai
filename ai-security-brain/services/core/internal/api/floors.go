package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/ai-security-brain/asb-core/internal/models"
)

func handleListFloors(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "id")
		floors, err := deps.Postgres.GetFloors(r.Context(), facilityID)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query floors")
			return
		}
		if floors == nil {
			floors = []models.Floor{}
		}
		writeJSON(w, http.StatusOK, floors)
	}
}

func handleCreateFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "id")
		var req struct {
			FloorNumber int    `json:"floor_number"`
			Name        string `json:"name"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		if req.Name == "" {
			req.Name = "New Floor"
		}
		if req.FloorNumber < 1 {
			req.FloorNumber = 1
		}
		f := models.Floor{
			FacilityID:  facilityID,
			FloorNumber: req.FloorNumber,
			Name:        req.Name,
		}
		result, err := deps.Postgres.CreateFloor(r.Context(), f)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to create floor: "+err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, result)
	}
}

func handleGetFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		f, err := deps.Postgres.GetFloor(r.Context(), floorID)
		if err != nil {
			writeError(w, http.StatusNotFound, "floor not found")
			return
		}
		writeJSON(w, http.StatusOK, f)
	}
}

func handleUpdateFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		var req struct {
			FloorNumber int    `json:"floor_number"`
			Name        string `json:"name"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		f := models.Floor{ID: floorID, FloorNumber: req.FloorNumber, Name: req.Name}
		if err := deps.Postgres.UpdateFloor(r.Context(), f); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to update floor")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "updated"})
	}
}

func handleDeleteFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		if err := deps.Postgres.DeleteFloor(r.Context(), floorID); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to delete floor")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
	}
}
