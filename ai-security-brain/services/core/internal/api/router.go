package api

import (
	"github.com/go-chi/chi/v5"

	"github.com/ai-security-brain/asb-core/internal/classifier"
	"github.com/ai-security-brain/asb-core/internal/models"
	"github.com/ai-security-brain/asb-core/internal/store"
	"github.com/ai-security-brain/asb-core/internal/ws"
)

// Dependencies holds all shared resources needed by the API handlers.
type Dependencies struct {
	ClickHouse         *store.ClickHouseStore
	Postgres           *store.PostgresStore
	Classifier         *classifier.Classifier
	Ingest             *ws.IngestHandler
	MapStore           *ws.MapStore
	GetLatestTelemetry func(robotID string) *models.TelemetryEvent
	GetStatus          func() models.SystemStatus
	Auth               AuthConfig
}

// NewRouter creates a chi.Router with all API endpoints mounted.
func NewRouter(deps Dependencies) chi.Router {
	r := chi.NewRouter()

	// Public endpoints (no auth required)
	r.Post("/auth/login", handleLogin(deps.Auth))
	r.Get("/health", handleHealth())
	r.Get("/status", handleStatus(deps))

	// Robot-accessible endpoints (robot API key via ?key= query param)
	r.Group(func(r chi.Router) {
		r.Use(RobotKeyMiddleware(deps.Auth))
		r.Get("/robots/{id}/boundaries", handleGetRobotBoundaries(deps))
	})

	// Protected endpoints (JWT required)
	r.Group(func(r chi.Router) {
		r.Use(JWTMiddleware(deps.Auth))

		r.Get("/telemetry/latest", handleLatestTelemetry(deps))

		r.Get("/incidents", handleListIncidents(deps))
		r.Get("/incidents/{id}", handleGetIncident(deps))

		r.Get("/metrics", handleMetrics(deps))

		r.Get("/config/thresholds", handleGetThresholds(deps))
		r.Post("/config/thresholds", handleUpdateThresholds(deps))

		r.Post("/demo/reset", handleReset(deps))

		r.Get("/platforms", handleListPlatforms(deps))

		r.Post("/robots", handleCreateRobot(deps))
		r.Delete("/robots/{id}", handleDeleteRobot(deps))
		r.Put("/robots/{id}/assignment", handleUpdateRobotAssignment(deps))
		r.Post("/robots/{id}/activate", handleActivateRobot(deps))
		r.Post("/robots/{id}/deactivate", handleDeactivateRobot(deps))
		r.Post("/robots/{id}/start", handleStartRobot(deps))

		r.Get("/facilities", handleListFacilities(deps))
		r.Post("/facilities", handleCreateFacility(deps))
		r.Get("/facilities/{id}", handleGetFacility(deps))
		r.Put("/facilities/{id}", handleUpdateFacility(deps))
		r.Delete("/facilities/{id}", handleDeleteFacility(deps))
		// Legacy facility-level floor plan routes (resolve to default floor)
		r.Post("/facilities/{id}/floor-plan", handleUploadFloorPlan(deps))
		r.Get("/facilities/{id}/floor-plan", handleGetFloorPlan(deps))
		r.Delete("/facilities/{id}/floor-plan", handleDeleteFloorPlan(deps))
		r.Post("/facilities/{id}/calibration", handleSaveCalibration(deps))
		r.Get("/facilities/{id}/calibration", handleGetCalibration(deps))

		// Floors under a facility
		r.Get("/facilities/{id}/floors", handleListFloors(deps))
		r.Post("/facilities/{id}/floors", handleCreateFloor(deps))
		r.Get("/floors/{floorId}", handleGetFloor(deps))
		r.Put("/floors/{floorId}", handleUpdateFloor(deps))
		r.Delete("/floors/{floorId}", handleDeleteFloor(deps))

		// Floor-level floor plan and calibration
		r.Post("/floors/{floorId}/floor-plan", handleUploadFloorPlanForFloor(deps))
		r.Get("/floors/{floorId}/floor-plan", handleGetFloorPlanForFloor(deps))
		r.Delete("/floors/{floorId}/floor-plan", handleDeleteFloorPlanForFloor(deps))
		r.Post("/floors/{floorId}/calibration", handleSaveCalibrationForFloor(deps))
		r.Get("/floors/{floorId}/calibration", handleGetCalibrationForFloor(deps))

		// Rooms under a floor
		r.Get("/floors/{floorId}/rooms", handleListRooms(deps))
		r.Post("/floors/{floorId}/rooms", handleCreateRoom(deps))
		r.Put("/rooms/{roomId}", handleUpdateRoom(deps))
		r.Delete("/rooms/{roomId}", handleDeleteRoom(deps))

		r.Get("/robots", handleListAllRobots(deps))
		r.Get("/robots/{id}", handleGetRobot(deps))

		r.Post("/reports/generate", handleGenerateReport(deps))

		r.Get("/compliance/summary", handleComplianceSummary(deps))
		r.Get("/compliance/incidents", handleComplianceIncidents(deps))
		r.Get("/compliance/requirements", handleComplianceRequirements(deps))
		r.Get("/compliance/status", handleComplianceStatus(deps))

		// Maps
		r.Get("/maps/{facilityId}/live", handleGetLiveMap(deps))
		r.Post("/maps/{facilityId}/reset", handleResetMap(deps))

		// Discovery & deployment (admin only)
		r.Post("/discovery/scan", handleDiscoveryScan(deps))
		r.Post("/discovery/deploy", handleDiscoveryDeploy(deps))
		r.Post("/discovery/check", handleDiscoveryCheck(deps))
		r.Post("/discovery/stop", handleDiscoveryStop(deps))
		r.Post("/discovery/restart", handleDiscoveryRestart(deps))
		r.Post("/discovery/uninstall", handleDiscoveryUninstall(deps))
	})

	return r
}
