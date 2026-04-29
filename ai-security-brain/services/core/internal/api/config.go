package api

import (
	"encoding/json"
	"net/http"

	"github.com/ai-security-brain/asb-core/internal/models"
)

func handleGetThresholds(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		t, err := deps.Postgres.GetThresholds(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to load thresholds")
			return
		}
		writeJSON(w, http.StatusOK, t)
	}
}

func handleUpdateThresholds(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var t models.Thresholds
		if err := json.NewDecoder(r.Body).Decode(&t); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}

		// Validate ranges.
		if t.ProximityCm < 5 || t.ProximityCm > 200 {
			writeError(w, http.StatusBadRequest, "proximity_cm must be between 5 and 200")
			return
		}
		if t.SpeedMax < 10 || t.SpeedMax > 100 {
			writeError(w, http.StatusBadRequest, "speed_max must be between 10 and 100")
			return
		}

		if err := deps.Postgres.UpdateThresholds(r.Context(), t); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to save thresholds")
			return
		}

		// Hot-reload into the classifier.
		deps.Classifier.UpdateThresholds(t)

		writeJSON(w, http.StatusOK, t)
	}
}
