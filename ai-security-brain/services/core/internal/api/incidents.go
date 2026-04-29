package api

import (
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/ai-security-brain/asb-core/internal/models"
	"github.com/ai-security-brain/asb-core/internal/store"
)

func handleListIncidents(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		if limit <= 0 {
			limit = 50
		}
		if limit > 200 {
			limit = 200
		}

		offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
		if offset < 0 {
			offset = 0
		}

		eventType := r.URL.Query().Get("type")
		severity := r.URL.Query().Get("severity")
		facilityID := r.URL.Query().Get("facility_id")
		robotType := r.URL.Query().Get("robot_type")
		vendor := r.URL.Query().Get("vendor")

		incidents, err := deps.Postgres.GetIncidentsFiltered(r.Context(), store.IncidentFilter{
			Limit:      limit,
			Offset:     offset,
			EventType:  eventType,
			Severity:   severity,
			FacilityID: facilityID,
			RobotType:  robotType,
			Vendor:     vendor,
		})
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query incidents")
			return
		}

		total, err := deps.Postgres.GetIncidentCount(r.Context(), time.Time{}, eventType)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to count incidents")
			return
		}

		if incidents == nil {
			incidents = []models.ClassifiedEvent{}
		}

		w.Header().Set("X-Total-Count", fmt.Sprintf("%d", total))
		writeJSON(w, http.StatusOK, incidents)
	}
}

func handleGetIncident(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := chi.URLParam(r, "id")

		incident, err := deps.Postgres.GetIncident(r.Context(), id)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query incident")
			return
		}
		if incident == nil {
			writeError(w, http.StatusNotFound, "incident not found")
			return
		}

		start := incident.OccurredAt.Add(-5 * time.Second)
		end := incident.OccurredAt.Add(5 * time.Second)
		telemetry, err := deps.ClickHouse.GetTelemetryRange(r.Context(), incident.RobotID, start, end)
		if err != nil {
			telemetry = nil
		}
		if telemetry == nil {
			telemetry = []models.TelemetryEvent{}
		}

		detail := models.IncidentDetail{
			Incident:         *incident,
			TelemetryContext: telemetry,
		}
		writeJSON(w, http.StatusOK, detail)
	}
}
