package api

import "net/http"

func handleStatus(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		status := deps.GetStatus()
		writeJSON(w, http.StatusOK, status)
	}
}
