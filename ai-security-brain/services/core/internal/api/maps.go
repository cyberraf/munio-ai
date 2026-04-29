package api

import (
	"net/http"

	"github.com/go-chi/chi/v5"
)

func handleGetLiveMap(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "facilityId")

		if deps.MapStore == nil {
			writeJSON(w, http.StatusOK, map[string]any{
				"facility_id": facilityID,
				"robots":      map[string]any{},
				"last_update":  0,
			})
			return
		}

		// Build a robot -> facility map from the registry.
		allRobots, _ := deps.Postgres.GetRegistryRobots(r.Context(), "")
		robotFacilities := make(map[string]string, len(allRobots))
		for _, rb := range allRobots {
			robotFacilities[rb.RobotID] = rb.FacilityID
		}

		fm := deps.MapStore.GetFacilityMap(facilityID, robotFacilities)
		writeJSON(w, http.StatusOK, fm)
	}
}

func handleResetMap(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if deps.MapStore != nil {
			deps.MapStore.Reset()
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "reset"})
	}
}
