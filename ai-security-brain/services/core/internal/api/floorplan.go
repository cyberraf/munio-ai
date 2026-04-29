package api

import (
	"encoding/json"
	"fmt"
	"image"
	"image/jpeg"
	_ "image/jpeg"
	"image/png"
	_ "image/png"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/ai-security-brain/asb-core/internal/models"
)

const (
	maxUploadSize   = 20 << 20 // 20 MB
	maxImageDim     = 4000     // max px on longest side
	thumbnailWidth  = 400
	floorPlanDir    = "data/floor-plans"
)

func handleUploadFloorPlan(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "id")

		r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)
		if err := r.ParseMultipartForm(maxUploadSize); err != nil {
			writeError(w, http.StatusBadRequest, "file too large (max 20MB)")
			return
		}

		file, header, err := r.FormFile("file")
		if err != nil {
			writeError(w, http.StatusBadRequest, "missing file field")
			return
		}
		defer file.Close()

		// Validate content type
		ct := header.Header.Get("Content-Type")
		if !strings.HasPrefix(ct, "image/") && ct != "application/pdf" {
			writeError(w, http.StatusBadRequest, "file must be an image (PNG, JPG) or PDF")
			return
		}

		// For SVGs, store directly without image processing
		if ct == "image/svg+xml" {
			dir := filepath.Join(floorPlanDir, facilityID)
			os.MkdirAll(dir, 0755)
			outPath := filepath.Join(dir, "plan.svg")
			out, err := os.Create(outPath)
			if err != nil {
				writeError(w, http.StatusInternalServerError, "failed to save file")
				return
			}
			io.Copy(out, file)
			out.Close()

			url := fmt.Sprintf("/data/floor-plans/%s/plan.svg", facilityID)
			deps.Postgres.UpdateFacilityFloorPlan(r.Context(), facilityID, url, 0, 0)
			writeJSON(w, http.StatusOK, map[string]any{
				"url": url, "width": 0, "height": 0,
			})
			return
		}

		// Decode image
		img, format, err := image.Decode(file)
		if err != nil {
			writeError(w, http.StatusBadRequest, "could not decode image — must be PNG or JPG")
			return
		}
		_ = format

		bounds := img.Bounds()
		origW := bounds.Dx()
		origH := bounds.Dy()

		// Resize if needed (max 4000px on longest side)
		newW, newH := origW, origH
		if origW > maxImageDim || origH > maxImageDim {
			if origW > origH {
				newW = maxImageDim
				newH = origH * maxImageDim / origW
			} else {
				newH = maxImageDim
				newW = origW * maxImageDim / origH
			}
			img = resizeImage(img, newW, newH)
		}

		// Save main image
		dir := filepath.Join(floorPlanDir, facilityID)
		os.MkdirAll(dir, 0755)

		mainPath := filepath.Join(dir, "plan.png")
		mainFile, err := os.Create(mainPath)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to save image")
			return
		}
		png.Encode(mainFile, img)
		mainFile.Close()

		// Save thumbnail
		thumbH := thumbnailWidth * newH / newW
		thumbDst := resizeImage(img, thumbnailWidth, thumbH)
		thumbPath := filepath.Join(dir, "thumb.jpg")
		thumbFile, err := os.Create(thumbPath)
		if err == nil {
			jpeg.Encode(thumbFile, thumbDst, &jpeg.Options{Quality: 75})
			thumbFile.Close()
		}

		url := fmt.Sprintf("/data/floor-plans/%s/plan.png", facilityID)
		if err := deps.Postgres.UpdateFacilityFloorPlan(r.Context(), facilityID, url, newW, newH); err != nil {
			log.Printf("[floor-plan] db update error: %v", err)
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"url":    url,
			"width":  newW,
			"height": newH,
		})
	}
}

func handleGetFloorPlan(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "id")

		facilities, err := deps.Postgres.GetFacilities(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to query facility")
			return
		}

		for _, f := range facilities {
			if f.ID == facilityID {
				if f.FloorPlanURL == "" {
					writeError(w, http.StatusNotFound, "no floor plan uploaded")
					return
				}
				writeJSON(w, http.StatusOK, map[string]any{
					"url":    f.FloorPlanURL,
					"width":  f.FloorPlanWidth,
					"height": f.FloorPlanHeight,
				})
				return
			}
		}
		writeError(w, http.StatusNotFound, "facility not found")
	}
}

func handleDeleteFloorPlan(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "id")

		// Remove files
		dir := filepath.Join(floorPlanDir, facilityID)
		os.RemoveAll(dir)

		// Clear DB
		if err := deps.Postgres.ClearFacilityFloorPlan(r.Context(), facilityID); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to clear floor plan")
			return
		}

		writeJSON(w, http.StatusOK, map[string]string{"status": "removed"})
	}
}

func handleSaveCalibration(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "id")
		var cal models.FloorPlanCalibration
		if err := json.NewDecoder(r.Body).Decode(&cal); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON")
			return
		}
		if cal.PixelsPerMeter <= 0 {
			writeError(w, http.StatusBadRequest, "pixels_per_meter must be positive")
			return
		}
		cal.FacilityID = facilityID
		if err := deps.Postgres.SaveCalibration(r.Context(), cal); err != nil {
			log.Printf("[calibration] save error: %v", err)
			writeError(w, http.StatusInternalServerError, "failed to save calibration")
			return
		}
		writeJSON(w, http.StatusOK, cal)
	}
}

func handleGetCalibration(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		facilityID := chi.URLParam(r, "id")
		cal, err := deps.Postgres.GetCalibration(r.Context(), facilityID)
		if err != nil {
			writeError(w, http.StatusNotFound, "no calibration found")
			return
		}
		writeJSON(w, http.StatusOK, cal)
	}
}

// ─── Floor-level floor plan handlers ────────────────────────────────────────
// These mirror the facility-level handlers above but operate on a specific
// floor (UUID) instead of a facility ID. The file storage path becomes
// data/floor-plans/{facilityId}/{floorId}/plan.png.

func handleUploadFloorPlanForFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		floor, err := deps.Postgres.GetFloor(r.Context(), floorID)
		if err != nil || floor == nil {
			writeError(w, http.StatusNotFound, "floor not found")
			return
		}

		r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)
		if err := r.ParseMultipartForm(maxUploadSize); err != nil {
			writeError(w, http.StatusBadRequest, "file too large (max 20MB)")
			return
		}

		file, _, err := r.FormFile("file")
		if err != nil {
			writeError(w, http.StatusBadRequest, "missing file field")
			return
		}
		defer file.Close()

		img, _, err := image.Decode(file)
		if err != nil {
			writeError(w, http.StatusBadRequest, "could not decode image")
			return
		}

		bounds := img.Bounds()
		newW, newH := bounds.Dx(), bounds.Dy()
		if newW > maxImageDim || newH > maxImageDim {
			if newW > newH {
				newH = newH * maxImageDim / newW
				newW = maxImageDim
			} else {
				newW = newW * maxImageDim / newH
				newH = maxImageDim
			}
			img = resizeImage(img, newW, newH)
		}

		dir := filepath.Join(floorPlanDir, floor.FacilityID, floorID)
		os.MkdirAll(dir, 0755)

		mainPath := filepath.Join(dir, "plan.png")
		mainFile, err := os.Create(mainPath)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "failed to save image")
			return
		}
		png.Encode(mainFile, img)
		mainFile.Close()

		url := fmt.Sprintf("/data/floor-plans/%s/%s/plan.png", floor.FacilityID, floorID)
		deps.Postgres.UpdateFloorFloorPlan(r.Context(), floorID, url, newW, newH)

		writeJSON(w, http.StatusOK, map[string]any{
			"url": url, "width": newW, "height": newH,
		})
	}
}

func handleGetFloorPlanForFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		floor, err := deps.Postgres.GetFloor(r.Context(), floorID)
		if err != nil || floor == nil {
			writeError(w, http.StatusNotFound, "floor not found")
			return
		}
		if floor.FloorPlanURL == "" {
			writeError(w, http.StatusNotFound, "no floor plan uploaded")
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"url":    floor.FloorPlanURL,
			"width":  floor.FloorPlanWidth,
			"height": floor.FloorPlanHeight,
		})
	}
}

func handleDeleteFloorPlanForFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		floor, err := deps.Postgres.GetFloor(r.Context(), floorID)
		if err != nil || floor == nil {
			writeError(w, http.StatusNotFound, "floor not found")
			return
		}
		dir := filepath.Join(floorPlanDir, floor.FacilityID, floorID)
		os.RemoveAll(dir)
		deps.Postgres.ClearFloorFloorPlan(r.Context(), floorID)
		writeJSON(w, http.StatusOK, map[string]string{"status": "removed"})
	}
}

func handleSaveCalibrationForFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		floor, err := deps.Postgres.GetFloor(r.Context(), floorID)
		if err != nil || floor == nil {
			writeError(w, http.StatusNotFound, "floor not found")
			return
		}
		var cal models.FloorPlanCalibration
		if err := json.NewDecoder(r.Body).Decode(&cal); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON")
			return
		}
		if cal.PixelsPerMeter <= 0 {
			writeError(w, http.StatusBadRequest, "pixels_per_meter must be positive")
			return
		}
		cal.FacilityID = floor.FacilityID
		cal.FloorID = floorID
		if err := deps.Postgres.SaveCalibrationByFloor(r.Context(), cal); err != nil {
			log.Printf("[calibration] save error for floor %s: %v", floorID, err)
			writeError(w, http.StatusInternalServerError, "failed to save calibration")
			return
		}
		writeJSON(w, http.StatusOK, cal)
	}
}

func handleGetCalibrationForFloor(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		floorID := chi.URLParam(r, "floorId")
		cal, err := deps.Postgres.GetCalibrationByFloor(r.Context(), floorID)
		if err != nil {
			writeError(w, http.StatusNotFound, "no calibration found for this floor")
			return
		}
		writeJSON(w, http.StatusOK, cal)
	}
}

// resizeImage scales an image to the given dimensions using nearest-neighbor (stdlib only).
func resizeImage(src image.Image, width, height int) *image.RGBA {
	dst := image.NewRGBA(image.Rect(0, 0, width, height))
	sb := src.Bounds()
	srcW := sb.Dx()
	srcH := sb.Dy()
	for y := 0; y < height; y++ {
		sy := sb.Min.Y + y*srcH/height
		for x := 0; x < width; x++ {
			sx := sb.Min.X + x*srcW/width
			dst.Set(x, y, src.At(sx, sy))
		}
	}
	return dst
}
