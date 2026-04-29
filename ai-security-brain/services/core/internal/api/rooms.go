package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/ai-security-brain/asb-core/internal/models"
)

func handleListRooms(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		rooms, err := deps.Postgres.GetRooms(r.Context(), floorID)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query rooms")
			return
		}
		if rooms == nil {
			rooms = []models.Room{}
		}
		writeJSON(w, http.StatusOK, rooms)
	}
}

func handleCreateRoom(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		var req models.Room
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		if req.Label == "" {
			writeError(w, http.StatusBadRequest, "label is required")
			return
		}
		req.FloorID = floorID
		if req.RoomType == "" {
			req.RoomType = "general"
		}
		if req.PolygonPoints == nil {
			req.PolygonPoints = json.RawMessage(`[]`)
		}
		result, err := deps.Postgres.CreateRoom(r.Context(), req)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to create room: "+err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, result)
	}
}

func handleUpdateRoom(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		roomID := chi.URLParam(r, "roomId")
		// Decode into a map so we only update fields the client actually sent.
		var raw map[string]json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}

		fields := make(map[string]any)
		if v, ok := raw["label"]; ok {
			var s string
			json.Unmarshal(v, &s)
			fields["label"] = s
		}
		if v, ok := raw["room_type"]; ok {
			var s string
			json.Unmarshal(v, &s)
			fields["room_type"] = s
		}
		if v, ok := raw["polygon_points"]; ok {
			fields["polygon_points"] = []byte(v)
		}
		if v, ok := raw["color"]; ok {
			var s string
			json.Unmarshal(v, &s)
			fields["color"] = s
		}
		if len(fields) == 0 {
			writeError(w, http.StatusBadRequest, "no fields to update")
			return
		}

		if err := deps.Postgres.PartialUpdateRoom(r.Context(), roomID, fields); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to update room")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "updated"})
	}
}

func handleDeleteRoom(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		roomID := chi.URLParam(r, "roomId")
		if err := deps.Postgres.DeleteRoom(r.Context(), roomID); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to delete room")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
	}
}
