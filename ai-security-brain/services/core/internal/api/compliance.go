package api

import (
	"fmt"
	"net/http"
	"time"

	"github.com/ai-security-brain/asb-core/internal/models"
)

// ─── Response types ───────────────────────────────────────────────────────────

// overdueDeadline describes an incident whose external reporting deadline has passed.
type overdueDeadline struct {
	IncidentID   string    `json:"incident_id"`
	EventType    string    `json:"event_type"`
	Severity     string    `json:"severity"`
	OccurredAt   time.Time `json:"occurred_at"`
	DeadlineName string    `json:"deadline_name"`
	DeadlineAt   time.Time `json:"deadline_at"`
	HoursOverdue float64   `json:"hours_overdue"`
}

// frameworkStatus is the per-regulatory-framework slice within the status response.
type frameworkStatus struct {
	Framework         string            `json:"framework"`
	MonitoringActive  bool              `json:"monitoring_active"`
	ReportableLast24h int               `json:"reportable_last_24h"`
	ReportableLast7d  int               `json:"reportable_last_7d"`
	ReportableLast30d int               `json:"reportable_last_30d"`
	OverdueDeadlines  []overdueDeadline `json:"overdue_deadlines"`
	Status            string            `json:"status"` // "compliant" | "attention_needed" | "non_compliant"
}

// complianceStatusResponse is the full status dashboard payload.
type complianceStatusResponse struct {
	FacilityID       string            `json:"facility_id"`
	CheckedAt        time.Time         `json:"checked_at"`
	MonitoringActive bool              `json:"monitoring_active"`
	Frameworks       []frameworkStatus `json:"frameworks"`
	OverallStatus    string            `json:"overall_status"` // worst across all frameworks
}

// ─── Deadline configuration ───────────────────────────────────────────────────

type deadlineRule struct {
	duration     time.Duration
	name         string // human-readable (e.g. "15 days")
	criticalOnly bool   // if true, only CRITICAL incidents are subject to this deadline
}

// deadlineRules maps framework identifiers to their external reporting obligations.
// Frameworks with no mandatory external deadline (e.g. ISO_10218) are omitted.
var deadlineRules = map[string]deadlineRule{
	models.FrameworkEUAIAct:   {15 * 24 * time.Hour, "15 days", false},
	models.FrameworkOSHA1910:  {8 * time.Hour, "8 hours", true},
	"OSHA":                    {8 * time.Hour, "8 hours", true},
	models.FrameworkANSIR1508: {24 * time.Hour, "24 hours", true},
}

// statusFrameworks is the ordered list evaluated by the status endpoint.
var statusFrameworks = []string{
	models.FrameworkEUAIAct,
	models.FrameworkISO10218,
	models.FrameworkOSHA1910,
	models.FrameworkANSIR1508,
}

// ─── Shared helper ───────────────────────────────────────────────────────────

// parseDateRange reads optional ?start=YYYY-MM-DD and ?end=YYYY-MM-DD query
// params. If omitted, start defaults to 30 days ago and end to now (UTC). The
// end value is stretched to the last second of that calendar day.
func parseDateRange(r *http.Request) (start, end time.Time, err error) {
	now := time.Now().UTC()
	start = now.AddDate(0, 0, -30).Truncate(24 * time.Hour)
	end = now

	if s := r.URL.Query().Get("start"); s != "" {
		t, e := time.Parse("2006-01-02", s)
		if e != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("invalid start date — expected YYYY-MM-DD, got %q", s)
		}
		start = t.UTC()
	}
	if e := r.URL.Query().Get("end"); e != "" {
		t, e2 := time.Parse("2006-01-02", e)
		if e2 != nil {
			return time.Time{}, time.Time{}, fmt.Errorf("invalid end date — expected YYYY-MM-DD, got %q", e)
		}
		end = t.Add(24*time.Hour - time.Second).UTC() // inclusive end-of-day
	}
	return start, end, nil
}

// ─── GET /compliance/summary ─────────────────────────────────────────────────

// handleComplianceSummary returns aggregate compliance counts for a facility
// and time window.
//
//	Query params: facility_id (optional), start (YYYY-MM-DD), end (YYYY-MM-DD)
func handleComplianceSummary(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := r.URL.Query().Get("facility_id")

		start, end, err := parseDateRange(r)
		if err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}

		summary, err := deps.Postgres.GetComplianceSummary(r.Context(), facilityID, start, end)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query compliance summary")
			return
		}
		writeJSON(w, http.StatusOK, summary)
	}
}

// ─── GET /compliance/incidents ───────────────────────────────────────────────

// handleComplianceIncidents returns incidents that are reportable under the
// given regulatory framework within the requested time window.
//
//	Query params: facility_id (optional), framework (required), start, end
func handleComplianceIncidents(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := r.URL.Query().Get("facility_id")
		framework := r.URL.Query().Get("framework")
		if framework == "" {
			writeError(w, http.StatusBadRequest, "framework query parameter is required")
			return
		}

		start, end, err := parseDateRange(r)
		if err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}

		incidents, err := deps.Postgres.GetReportableIncidents(r.Context(), facilityID, framework, start, end)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query reportable incidents")
			return
		}
		if incidents == nil {
			incidents = []models.ClassifiedEvent{}
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"framework":  framework,
			"start":      start.Format("2006-01-02"),
			"end":        end.Format("2006-01-02"),
			"count":      len(incidents),
			"incidents":  incidents,
		})
	}
}

// ─── GET /compliance/requirements ────────────────────────────────────────────

// handleComplianceRequirements returns the seeded compliance requirements
// reference data, optionally filtered to a single regulatory framework.
//
//	Query params: framework (optional — EU_AI_ACT, ISO_10218, OSHA, ANSI_R15_08, IEC_62443)
func handleComplianceRequirements(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		framework := r.URL.Query().Get("framework")

		reqs, err := deps.Postgres.GetComplianceRequirements(r.Context(), framework)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query compliance requirements")
			return
		}
		if reqs == nil {
			reqs = []models.ComplianceRequirement{}
		}
		writeJSON(w, http.StatusOK, reqs)
	}
}

// ─── GET /compliance/status ──────────────────────────────────────────────────

// handleComplianceStatus returns a per-framework status dashboard for a
// facility, including monitoring liveness, incident counts in 24h/7d/30d
// windows, overdue reporting deadlines, and a rolled-up overall status.
//
//	Query params: facility_id (optional)
func handleComplianceStatus(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := r.URL.Query().Get("facility_id")
		ctx := r.Context()
		now := time.Now().UTC()

		// ── Monitoring liveness ───────────────────────────────────────────────
		// Monitoring is considered active if any robot in the facility reported
		// telemetry within the last 5 minutes (indicated by last_seen_at in the
		// registry, which is updated on every inbound WebSocket message).
		monitoringActive := false
		robots, err := deps.Postgres.GetRegistryRobots(ctx, facilityID)
		if err == nil {
			for _, rb := range robots {
				if rb.LastSeenAt != nil && now.Sub(*rb.LastSeenAt) < 5*time.Minute {
					monitoringActive = true
					break
				}
			}
		}

		// Shared look-back window for all per-framework incident queries.
		window30d := now.AddDate(0, 0, -30)

		// ── Per-framework status ──────────────────────────────────────────────
		statuses := make([]frameworkStatus, 0, len(statusFrameworks))
		overallWorst := "compliant"

		for _, fw := range statusFrameworks {
			incidents, err := deps.Postgres.GetReportableIncidents(ctx, facilityID, fw, window30d, now)
			if err != nil {
				writeError(w, http.StatusInternalServerError,
					fmt.Sprintf("failed to query incidents for framework %s", fw))
				return
			}

			// Bucket incidents by recency and detect overdue reporting deadlines
			// in a single pass — avoids three separate DB round trips per framework.
			var count24h, count7d int
			var overdue []overdueDeadline

			rule, hasDeadline := deadlineRules[fw]

			for _, inc := range incidents {
				age := now.Sub(inc.OccurredAt)
				if age <= 24*time.Hour {
					count24h++
				}
				if age <= 7*24*time.Hour {
					count7d++
				}

				if hasDeadline {
					if rule.criticalOnly && inc.Severity != models.SeverityCritical {
						continue
					}
					deadlineAt := inc.OccurredAt.Add(rule.duration)
					if now.After(deadlineAt) {
						overdue = append(overdue, overdueDeadline{
							IncidentID:   inc.ID,
							EventType:    inc.EventType,
							Severity:     inc.Severity,
							OccurredAt:   inc.OccurredAt,
							DeadlineName: rule.name,
							DeadlineAt:   deadlineAt,
							HoursOverdue: now.Sub(deadlineAt).Hours(),
						})
					}
				}
			}

			if overdue == nil {
				overdue = []overdueDeadline{}
			}

			// Determine this framework's compliance status. Priority: non_compliant
			// (overdue deadlines) > attention_needed (recent incidents or gap in
			// telemetry) > compliant.
			var fwStatus string
			switch {
			case len(overdue) > 0:
				fwStatus = "non_compliant"
			case count24h > 0 || !monitoringActive:
				fwStatus = "attention_needed"
			default:
				fwStatus = "compliant"
			}

			// Roll up to overall worst status.
			if fwStatus == "non_compliant" {
				overallWorst = "non_compliant"
			} else if fwStatus == "attention_needed" && overallWorst != "non_compliant" {
				overallWorst = "attention_needed"
			}

			statuses = append(statuses, frameworkStatus{
				Framework:         fw,
				MonitoringActive:  monitoringActive,
				ReportableLast24h: count24h,
				ReportableLast7d:  count7d,
				ReportableLast30d: len(incidents),
				OverdueDeadlines:  overdue,
				Status:            fwStatus,
			})
		}

		writeJSON(w, http.StatusOK, complianceStatusResponse{
			FacilityID:       facilityID,
			CheckedAt:        now,
			MonitoringActive: monitoringActive,
			Frameworks:       statuses,
			OverallStatus:    overallWorst,
		})
	}
}
