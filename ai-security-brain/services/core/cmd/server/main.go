package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"

	"github.com/ai-security-brain/asb-core/internal/api"
	"github.com/ai-security-brain/asb-core/internal/classifier"
	"github.com/ai-security-brain/asb-core/internal/models"
	"github.com/ai-security-brain/asb-core/internal/store"
	"github.com/ai-security-brain/asb-core/internal/ws"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	log.SetFlags(log.Ltime | log.Lmsgprefix)
	log.SetPrefix("[asb] ")

	startTime := time.Now()

	clickhouseURL := env("CLICKHOUSE_URL", "clickhouse://default:asb_dev@localhost:9000?database=default")
	postgresURL := env("POSTGRES_URL", "postgres://asb:asb_dev@localhost:5432/asb?sslmode=disable")
	port := env("PORT", "8080")

	// --- Data stores ---
	ch, err := store.NewClickHouseStore(clickhouseURL)
	if err != nil {
		log.Fatalf("clickhouse: %v", err)
	}
	log.Println("clickhouse connected")

	pg, err := store.NewPostgresStore(postgresURL)
	if err != nil {
		log.Fatalf("postgres: %v", err)
	}
	log.Println("postgres connected")

	// --- Thresholds & classifier ---
	thresholds, err := pg.GetThresholds(context.Background())
	if err != nil {
		log.Fatalf("load thresholds: %v", err)
	}
	cls := classifier.NewClassifier(*thresholds)
	log.Printf("classifier loaded (proximity=%.0fcm, speed_max=%.0f)", thresholds.ProximityCm, thresholds.SpeedMax)

	// --- Broadcaster & Map Store ---
	broadcaster := ws.NewBroadcaster()
	mapStore := ws.NewMapStore()

	// --- Buffered telemetry writer ---
	var bufMu sync.Mutex
	var buf []models.TelemetryEvent
	flushTicker := time.NewTicker(1 * time.Second)

	flush := func() {
		bufMu.Lock()
		if len(buf) == 0 {
			bufMu.Unlock()
			return
		}
		batch := buf
		buf = nil
		bufMu.Unlock()

		if err := ch.WriteTelemetryBatch(context.Background(), batch); err != nil {
			log.Printf("clickhouse batch write error: %v", err)
		}
	}

	go func() {
		for range flushTicker.C {
			flush()
		}
	}()

	addToBuffer := func(e models.TelemetryEvent) {
		bufMu.Lock()
		buf = append(buf, e)
		full := len(buf) >= 100
		bufMu.Unlock()
		if full {
			go flush()
		}
	}

	// --- Shared state ---
	var totalEvents atomic.Int64
	var latestMu sync.Mutex
	latestByRobot := make(map[string]*models.TelemetryEvent)

	// --- Command channel ---
	cmdCh := make(chan models.Command, 16)

	// --- Robot-to-facility lookup cache ---
	robotFacilityCache := make(map[string]string)
	var robotFacilityCacheMu sync.RWMutex

	lookupFacility := func(robotID string) string {
		robotFacilityCacheMu.RLock()
		fid, ok := robotFacilityCache[robotID]
		robotFacilityCacheMu.RUnlock()
		if ok {
			return fid
		}
		// Cache miss — query the registry
		robots, err := pg.GetRegistryRobots(context.Background(), "")
		if err == nil {
			robotFacilityCacheMu.Lock()
			for _, r := range robots {
				robotFacilityCache[r.RobotID] = r.FacilityID
			}
			robotFacilityCacheMu.Unlock()
			robotFacilityCacheMu.RLock()
			fid = robotFacilityCache[robotID]
			robotFacilityCacheMu.RUnlock()
		}
		return fid
	}

	// --- Registry heartbeat: refresh last_seen_at at most once per minute per robot ---
	var seenMu sync.Mutex
	lastSeen := make(map[string]time.Time)

	refreshSeen := func(robotID string) {
		seenMu.Lock()
		last := lastSeen[robotID]
		if time.Since(last) > time.Minute {
			lastSeen[robotID] = time.Now()
			seenMu.Unlock()
			if err := pg.UpdateRegistryRobotSeen(context.Background(), robotID); err != nil {
				log.Printf("[ingest] update last_seen_at for %q: %v", robotID, err)
			}
		} else {
			seenMu.Unlock()
		}
	}

	// --- Ingest handler ---
	onTelemetry := func(event models.TelemetryEvent) {
		totalEvents.Add(1)
		refreshSeen(event.RobotID)

		// Enrich facility_id from registry if missing
		if event.FacilityID == "" {
			event.FacilityID = lookupFacility(event.RobotID)
		}

		// Update per-robot latest telemetry.
		latestMu.Lock()
		cp := event
		latestByRobot[event.RobotID] = &cp
		latestMu.Unlock()

		// Buffer for ClickHouse batch write.
		addToBuffer(event)

		// Classify and store incidents.
		incidents := cls.Classify(event)
		for _, inc := range incidents {
			if err := pg.CreateIncidentMultiRobot(context.Background(), inc, event.RobotType, event.Vendor, event.FacilityID); err != nil {
				log.Printf("postgres create incident error: %v", err)
			}
			broadcaster.BroadcastAlert(inc)
			log.Printf("incident: %s %s — %s", inc.Severity, inc.EventType, inc.Description)
		}

		// Broadcast live telemetry to dashboards.
		broadcaster.BroadcastTelemetry(event)
	}

	onStatusChange := func(robotId, status string) {
		if err := pg.UpdateRobotStatus(context.Background(), robotId, status); err != nil {
			log.Printf("[ingest] update robot status %q → %s: %v", robotId, status, err)
		} else {
			log.Printf("[ingest] robot %q is now %s", robotId, status)
		}
		if status == "online" {
			pg.UpdateRegistryRobotStatus(context.Background(), robotId, "active")
		} else {
			pg.UpdateRegistryRobotStatus(context.Background(), robotId, "inactive")
		}
	}

	onMapUpdate := func(update models.MapUpdate) {
		mapStore.UpdateMap(update)
		data, err := json.Marshal(update)
		if err == nil {
			broadcaster.BroadcastMap(data)
		}
	}

	onSnapshot := func(snap models.CameraSnapshot) {
		mapStore.UpdateSnapshot(snap)
		data, err := json.Marshal(snap)
		if err == nil {
			broadcaster.BroadcastMap(data)
		}
	}

	ingestHandler := ws.NewIngestHandler(ws.IngestCallbacks{
		OnTelemetry:    onTelemetry,
		OnMapUpdate:    onMapUpdate,
		OnSnapshot:     onSnapshot,
		OnStatusChange: onStatusChange,
	}, cmdCh)
	commandHandler := ws.NewCommandHandler(cmdCh)

	// --- Auth config ---
	authCfg := api.LoadAuthConfig()
	log.Printf("auth: admin=%s, robot-key=%s…", authCfg.AdminEmail, authCfg.RobotAPIKey[:8])

	// --- API dependencies ---
	apiRouter := api.NewRouter(api.Dependencies{
		ClickHouse: ch,
		Postgres:   pg,
		Classifier: cls,
		Ingest:     ingestHandler,
		MapStore:   mapStore,
		Auth:       authCfg,
		GetLatestTelemetry: func(robotID string) *models.TelemetryEvent {
			latestMu.Lock()
			defer latestMu.Unlock()
			if robotID != "" {
				return latestByRobot[robotID]
			}
			// Return any latest if no robotID specified
			for _, v := range latestByRobot {
				return v
			}
			return nil
		},
		GetStatus: func() models.SystemStatus {
			dbStatus := "ok"
			if err := ch.Ping(context.Background()); err != nil {
				dbStatus = "degraded"
			}
			if err := pg.Ping(context.Background()); err != nil {
				dbStatus = "degraded"
			}
			return models.SystemStatus{
				RobotConnected: ingestHandler.IsRobotConnected(),
				UptimeSeconds:  time.Since(startTime).Seconds(),
				TotalEvents:    totalEvents.Load(),
				DbStatus:       dbStatus,
			}
		},
	})

	// --- Router ---
	r := chi.NewRouter()
	r.Use(middleware.Recoverer)
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{"http://localhost:3000", "http://localhost:3001"},
		AllowedMethods:   []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type"},
		AllowCredentials: true,
		MaxAge:           300,
	}))

	// WebSocket routes.
	wsAuth := api.WSAuthMiddleware(authCfg)
	robotAuth := api.RobotKeyMiddleware(authCfg)
	r.Handle("/ws/telemetry", robotAuth(ingestHandler))
	r.Handle("/ws/live", wsAuth(http.HandlerFunc(broadcaster.HandleLive)))
	r.Handle("/ws/alerts", wsAuth(http.HandlerFunc(broadcaster.HandleAlerts)))
	r.Handle("/ws/map", wsAuth(http.HandlerFunc(broadcaster.HandleMap)))
	r.Handle("/ws/command", wsAuth(commandHandler))

	// Static file serving for floor plans and snapshots.
	// `Cache-Control: no-cache` ensures the dashboard sees a freshly-converted
	// SVG immediately instead of a stale PNG cached under the same path.
	fileServer := http.StripPrefix("/data/", http.FileServer(http.Dir("data")))
	r.Get("/data/*", func(w http.ResponseWriter, req *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Cache-Control", "no-cache, must-revalidate")
		fileServer.ServeHTTP(w, req)
	})

	// API routes.
	r.Mount("/api", apiRouter)

	// --- HTTP server ---
	srv := &http.Server{
		Addr:    ":" + port,
		Handler: r,
	}

	// Graceful shutdown.
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("shutting down...")

		flushTicker.Stop()
		flush()

		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	}()

	log.Printf("ASB Core running on :%s", port)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("server error: %v", err)
	}
	log.Println("server stopped")
}
