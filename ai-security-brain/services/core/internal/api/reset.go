package api

import (
	"log"
	"net/http"
)

func handleReset(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := deps.ClickHouse.Truncate(r.Context()); err != nil {
			log.Printf("[reset] clickhouse truncate error: %v", err)
			writeError(w, http.StatusInternalServerError, "failed to reset telemetry data")
			return
		}
		if err := deps.Postgres.DeleteAllIncidents(r.Context()); err != nil {
			log.Printf("[reset] postgres delete error: %v", err)
			writeError(w, http.StatusInternalServerError, "failed to reset incident data")
			return
		}
		log.Println("[reset] demo data reset complete")
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	}
}
