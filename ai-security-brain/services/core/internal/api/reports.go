package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/go-pdf/fpdf"

	"github.com/ai-security-brain/asb-core/internal/models"
	"github.com/ai-security-brain/asb-core/internal/store"
)

type reportRequest struct {
	FacilityID string `json:"facility_id"`
	StartTime  string `json:"start_time"`
	EndTime    string `json:"end_time"`
}

func handleGenerateReport(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req reportRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}

		startTime, err := time.Parse(time.RFC3339, req.StartTime)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid start_time (RFC3339)")
			return
		}
		endTime, err := time.Parse(time.RFC3339, req.EndTime)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid end_time (RFC3339)")
			return
		}

		// Resolve facility name
		facilityName := req.FacilityID
		if req.FacilityID != "" {
			facilities, _ := deps.Postgres.GetFacilities(r.Context())
			for _, f := range facilities {
				if f.ID == req.FacilityID {
					facilityName = f.Name
					break
				}
			}
		}

		// Get incidents in range
		incidents, err := deps.Postgres.GetIncidentsFiltered(r.Context(), store.IncidentFilter{
			Limit:      1000,
			Offset:     0,
			FacilityID: req.FacilityID,
		})
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query incidents")
			return
		}

		// Filter by time range
		var filtered []models.ClassifiedEvent
		for _, inc := range incidents {
			if (inc.OccurredAt.Equal(startTime) || inc.OccurredAt.After(startTime)) &&
				(inc.OccurredAt.Equal(endTime) || inc.OccurredAt.Before(endTime)) {
				filtered = append(filtered, inc)
			}
		}

		// Severity counts
		severityCounts := map[string]int{"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
		estopCount := 0
		for _, inc := range filtered {
			severityCounts[inc.Severity]++
			if inc.EventType == models.EventEstopTriggered {
				estopCount++
			}
		}

		// Get overall metrics
		totalEvents, avgSpeed, _, _ := deps.ClickHouse.GetOverallMetrics(r.Context(), "", startTime)

		thresholds, _ := deps.Postgres.GetThresholds(r.Context())
		speedCompliance := 100.0
		if thresholds != nil && avgSpeed > thresholds.SpeedMax && totalEvents > 0 {
			speedCompliance = (thresholds.SpeedMax / avgSpeed) * 100.0
		}

		// Generate PDF
		pdf := fpdf.New("P", "mm", "A4", "")
		pdf.SetAutoPageBreak(true, 20)

		genTimestamp := time.Now().UTC().Format("2006-01-02 15:04:05 UTC")
		dateRange := fmt.Sprintf("%s to %s",
			startTime.Format("2006-01-02"),
			endTime.Format("2006-01-02"))

		// --- Page 1: Executive Summary ---
		pdf.AddPage()

		// Dark header bar
		pdf.SetFillColor(11, 15, 25) // #0B0F19
		pdf.Rect(0, 0, 210, 30, "F")
		pdf.SetTextColor(0, 201, 167) // #00C9A7
		pdf.SetFont("Helvetica", "B", 16)
		pdf.SetXY(15, 8)
		pdf.Cell(0, 8, "AI Security Brain")
		pdf.SetTextColor(232, 236, 241) // #E8ECF1
		pdf.SetFont("Helvetica", "", 10)
		pdf.SetXY(15, 18)
		pdf.Cell(0, 6, "Safety Report")

		// Facility + date info
		pdf.SetTextColor(60, 60, 60)
		pdf.SetFont("Helvetica", "", 10)
		pdf.SetXY(15, 38)
		pdf.Cell(0, 6, fmt.Sprintf("Facility: %s", facilityName))
		pdf.SetXY(15, 45)
		pdf.Cell(0, 6, fmt.Sprintf("Period: %s", dateRange))

		// Executive Summary heading
		pdf.SetTextColor(30, 30, 30)
		pdf.SetFont("Helvetica", "B", 13)
		pdf.SetXY(15, 58)
		pdf.Cell(0, 8, "Executive Summary")

		pdf.SetDrawColor(200, 200, 200)
		pdf.Line(15, 67, 195, 67)

		// Summary stats
		pdf.SetFont("Helvetica", "", 10)
		pdf.SetTextColor(60, 60, 60)
		y := 72.0

		summaryRows := []struct{ label, value string }{
			{"Total Telemetry Events", fmt.Sprintf("%d", totalEvents)},
			{"Total Incidents", fmt.Sprintf("%d", len(filtered))},
			{"Critical Incidents", fmt.Sprintf("%d", severityCounts["CRITICAL"])},
			{"High Severity", fmt.Sprintf("%d", severityCounts["HIGH"])},
			{"Medium Severity", fmt.Sprintf("%d", severityCounts["MEDIUM"])},
			{"Low Severity", fmt.Sprintf("%d", severityCounts["LOW"])},
			{"E-Stop Events", fmt.Sprintf("%d", estopCount)},
			{"Speed Compliance", fmt.Sprintf("%.1f%%", speedCompliance)},
		}

		for _, row := range summaryRows {
			pdf.SetXY(15, y)
			pdf.SetFont("Helvetica", "", 10)
			pdf.Cell(80, 6, row.label)
			pdf.SetFont("Helvetica", "B", 10)
			pdf.Cell(40, 6, row.value)
			y += 7
		}

		// Severity table
		y += 8
		pdf.SetFont("Helvetica", "B", 12)
		pdf.SetTextColor(30, 30, 30)
		pdf.SetXY(15, y)
		pdf.Cell(0, 8, "Incidents by Severity")
		y += 10

		// Table header
		pdf.SetFont("Helvetica", "B", 9)
		pdf.SetFillColor(240, 240, 240)
		pdf.SetTextColor(60, 60, 60)
		pdf.SetXY(15, y)
		pdf.CellFormat(60, 7, "Severity", "1", 0, "L", true, 0, "")
		pdf.CellFormat(40, 7, "Count", "1", 0, "C", true, 0, "")
		pdf.CellFormat(60, 7, "Percentage", "1", 1, "C", true, 0, "")
		y += 7

		pdf.SetFont("Helvetica", "", 9)
		pdf.SetFillColor(255, 255, 255)
		total := len(filtered)
		for _, sev := range []string{"CRITICAL", "HIGH", "MEDIUM", "LOW"} {
			count := severityCounts[sev]
			pct := 0.0
			if total > 0 {
				pct = float64(count) / float64(total) * 100
			}
			pdf.SetXY(15, y)
			pdf.CellFormat(60, 6, sev, "1", 0, "L", false, 0, "")
			pdf.CellFormat(40, 6, fmt.Sprintf("%d", count), "1", 0, "C", false, 0, "")
			pdf.CellFormat(60, 6, fmt.Sprintf("%.1f%%", pct), "1", 1, "C", false, 0, "")
			y += 6
		}

		// --- Page 2+: Incident Detail Table ---
		if len(filtered) > 0 {
			pdf.AddPage()

			pdf.SetFont("Helvetica", "B", 13)
			pdf.SetTextColor(30, 30, 30)
			pdf.SetXY(15, 15)
			pdf.Cell(0, 8, "Incident Detail")
			pdf.SetDrawColor(200, 200, 200)
			pdf.Line(15, 24, 195, 24)

			rowsPerPage := 30
			colWidths := []float64{25, 22, 38, 20, 45, 18, 12}
			headers := []string{"Time", "Robot", "Type", "Severity", "Description", "Dist", "Spd"}

			writeTableHeader := func(yPos float64) float64 {
				pdf.SetFont("Helvetica", "B", 7)
				pdf.SetFillColor(240, 240, 240)
				pdf.SetTextColor(60, 60, 60)
				pdf.SetXY(15, yPos)
				for i, h := range headers {
					pdf.CellFormat(colWidths[i], 6, h, "1", 0, "L", true, 0, "")
				}
				pdf.Ln(-1)
				return yPos + 6
			}

			y = writeTableHeader(28)
			rowCount := 0

			pdf.SetFont("Helvetica", "", 7)
			pdf.SetFillColor(255, 255, 255)
			pdf.SetTextColor(40, 40, 40)

			for _, inc := range filtered {
				if rowCount > 0 && rowCount%rowsPerPage == 0 {
					pdf.AddPage()
					y = writeTableHeader(15)
					pdf.SetFont("Helvetica", "", 7)
					pdf.SetTextColor(40, 40, 40)
				}

				ts := inc.OccurredAt.Format("01-02 15:04:05")
				desc := inc.Description
				if len(desc) > 40 {
					desc = desc[:40] + "..."
				}

				pdf.SetXY(15, y)
				pdf.CellFormat(colWidths[0], 5, ts, "1", 0, "L", false, 0, "")
				pdf.CellFormat(colWidths[1], 5, truncate(inc.RobotID, 14), "1", 0, "L", false, 0, "")
				pdf.CellFormat(colWidths[2], 5, inc.EventType, "1", 0, "L", false, 0, "")
				pdf.CellFormat(colWidths[3], 5, inc.Severity, "1", 0, "L", false, 0, "")
				pdf.CellFormat(colWidths[4], 5, desc, "1", 0, "L", false, 0, "")
				pdf.CellFormat(colWidths[5], 5, fmt.Sprintf("%.1f", inc.DistanceCm), "1", 0, "R", false, 0, "")
				pdf.CellFormat(colWidths[6], 5, fmt.Sprintf("%.0f", inc.Speed), "1", 1, "R", false, 0, "")
				y += 5
				rowCount++
			}
		}

		// Footer on all pages
		totalPages := pdf.PageCount()
		for i := 1; i <= totalPages; i++ {
			pdf.SetPage(i)
			pdf.SetXY(15, 282)
			pdf.SetFont("Helvetica", "", 7)
			pdf.SetTextColor(150, 150, 150)
			pdf.Cell(0, 5, fmt.Sprintf("Generated by AI Security Brain · %s · Page %d of %d", genTimestamp, i, totalPages))
		}

		// Write PDF response
		w.Header().Set("Content-Type", "application/pdf")
		w.Header().Set("Content-Disposition",
			fmt.Sprintf(`attachment; filename="asb_report_%s_%s.pdf"`,
				req.FacilityID, startTime.Format("20060102")))
		if err := pdf.Output(w); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to generate PDF")
		}
	}
}

func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-1] + "…"
}
