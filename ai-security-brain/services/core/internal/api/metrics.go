package api

import (
	"net/http"
	"time"

	"github.com/ai-security-brain/asb-core/internal/models"
)

var rangeDurations = map[string]time.Duration{
	"1h":  1 * time.Hour,
	"6h":  6 * time.Hour,
	"24h": 24 * time.Hour,
	"7d":  7 * 24 * time.Hour,
}

func handleMetrics(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rangeStr := r.URL.Query().Get("range")
		dur, ok := rangeDurations[rangeStr]
		if !ok {
			dur = 1 * time.Hour
		}
		since := time.Now().UTC().Add(-dur)

		robotID := r.URL.Query().Get("robot_id")
		facilityID := r.URL.Query().Get("facility_id")

		// ClickHouse aggregates — use robotID if provided, otherwise all.
		queryRobotID := robotID // empty = all robots
		totalEvents, avgSpeed, minDistance, err := deps.ClickHouse.GetOverallMetrics(r.Context(), queryRobotID, since)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query telemetry metrics")
			return
		}

		thresholds, err := deps.Postgres.GetThresholds(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to load thresholds")
			return
		}
		var speedCompliancePct float64
		if totalEvents > 0 {
			speedCompliancePct = 100.0
			if avgSpeed > thresholds.SpeedMax {
				speedCompliancePct = (thresholds.SpeedMax / avgSpeed) * 100.0
			}
		}

		totalIncidents, err := deps.Postgres.GetIncidentCount(r.Context(), since, "")
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to count incidents")
			return
		}
		totalEstops, err := deps.Postgres.GetIncidentCount(r.Context(), since, models.EventEstopTriggered)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to count estops")
			return
		}

		hoursMonitored := dur.Hours()
		if totalEvents == 0 {
			hoursMonitored = 0
		}

		// Hourly breakdown — use facility-level view if facility_id is set.
		var hourly []models.HourlyMetric
		if facilityID != "" {
			hourly, err = deps.ClickHouse.GetHourlyMetricsByFacility(r.Context(), facilityID, since)
		} else {
			hourly, err = deps.ClickHouse.GetHourlyMetrics(r.Context(), queryRobotID, since)
		}
		if err != nil {
			hourly = nil
		}
		if hourly == nil {
			hourly = []models.HourlyMetric{}
		}

		metrics := models.Metrics{
			TotalEvents:        totalEvents,
			TotalIncidents:     totalIncidents,
			TotalEstops:        totalEstops,
			AvgSpeed:           avgSpeed,
			MinDistance:         minDistance,
			HoursMonitored:     hoursMonitored,
			SpeedCompliancePct: speedCompliancePct,
			HourlyBreakdown:    hourly,
		}
		writeJSON(w, http.StatusOK, metrics)
	}
}
