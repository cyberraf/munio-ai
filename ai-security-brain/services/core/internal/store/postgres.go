package store

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/ai-security-brain/asb-core/internal/models"
	"time"
)

// PostgresStore wraps a pgxpool connection to PostgreSQL.
type PostgresStore struct {
	pool *pgxpool.Pool
}

// NewPostgresStore connects to PostgreSQL and verifies with a ping.
func NewPostgresStore(connStr string) (*PostgresStore, error) {
	pool, err := pgxpool.New(context.Background(), connStr)
	if err != nil {
		return nil, fmt.Errorf("postgres connect: %w", err)
	}
	if err := pool.Ping(context.Background()); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres ping: %w", err)
	}
	return &PostgresStore{pool: pool}, nil
}

// CreateIncident inserts a classified event into the incidents table.
func (s *PostgresStore) CreateIncident(ctx context.Context, e models.ClassifiedEvent) error {
	const q = `INSERT INTO incidents (
		id, robot_id, event_type, severity, description,
		distance_cm, speed, steering_angle, occurred_at
	) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`

	_, err := s.pool.Exec(ctx, q,
		e.ID, e.RobotID, e.EventType, e.Severity, e.Description,
		e.DistanceCm, e.Speed, e.SteeringAngle, e.OccurredAt,
	)
	return err
}

// IncidentFilter holds optional filter params for incident queries.
type IncidentFilter struct {
	Limit      int
	Offset     int
	EventType  string
	Severity   string
	FacilityID string
	RobotType  string
	Vendor     string
}

// GetIncidents returns incidents with optional filtering, ordered by occurred_at DESC.
func (s *PostgresStore) GetIncidents(ctx context.Context, limit, offset int, eventType, severity string) ([]models.ClassifiedEvent, error) {
	return s.GetIncidentsFiltered(ctx, IncidentFilter{
		Limit:     limit,
		Offset:    offset,
		EventType: eventType,
		Severity:  severity,
	})
}

// GetIncidentsFiltered returns incidents matching all provided filter criteria.
func (s *PostgresStore) GetIncidentsFiltered(ctx context.Context, f IncidentFilter) ([]models.ClassifiedEvent, error) {
	var conditions []string
	var args []any
	argIdx := 1

	add := func(col, val string) {
		if val != "" {
			conditions = append(conditions, fmt.Sprintf("%s = $%d", col, argIdx))
			args = append(args, val)
			argIdx++
		}
	}
	add("event_type", f.EventType)
	add("severity", f.Severity)
	add("facility_id", f.FacilityID)
	add("robot_type", f.RobotType)
	add("vendor", f.Vendor)

	q := "SELECT id, robot_id, event_type, severity, description, distance_cm, speed, steering_angle, occurred_at, created_at FROM incidents"
	if len(conditions) > 0 {
		q += " WHERE " + strings.Join(conditions, " AND ")
	}
	q += fmt.Sprintf(" ORDER BY occurred_at DESC LIMIT $%d OFFSET $%d", argIdx, argIdx+1)
	args = append(args, f.Limit, f.Offset)

	rows, err := s.pool.Query(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("postgres get incidents: %w", err)
	}
	defer rows.Close()

	var incidents []models.ClassifiedEvent
	for rows.Next() {
		var e models.ClassifiedEvent
		if err := rows.Scan(
			&e.ID, &e.RobotID, &e.EventType, &e.Severity, &e.Description,
			&e.DistanceCm, &e.Speed, &e.SteeringAngle, &e.OccurredAt, &e.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("postgres scan incident: %w", err)
		}
		incidents = append(incidents, e)
	}
	return incidents, rows.Err()
}

// GetIncident returns a single incident by UUID.
func (s *PostgresStore) GetIncident(ctx context.Context, id string) (*models.ClassifiedEvent, error) {
	const q = `SELECT id, robot_id, event_type, severity, description,
		distance_cm, speed, steering_angle, occurred_at, created_at
	FROM incidents WHERE id = $1`

	var e models.ClassifiedEvent
	err := s.pool.QueryRow(ctx, q, id).Scan(
		&e.ID, &e.RobotID, &e.EventType, &e.Severity, &e.Description,
		&e.DistanceCm, &e.Speed, &e.SteeringAngle, &e.OccurredAt, &e.CreatedAt,
	)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("postgres get incident: %w", err)
	}
	return &e, nil
}

// GetIncidentCount returns the number of incidents since a given time, with optional type filter.
func (s *PostgresStore) GetIncidentCount(ctx context.Context, since time.Time, eventType string) (int64, error) {
	var count int64
	if eventType != "" {
		err := s.pool.QueryRow(ctx,
			"SELECT count(*) FROM incidents WHERE occurred_at >= $1 AND event_type = $2",
			since, eventType,
		).Scan(&count)
		return count, err
	}
	err := s.pool.QueryRow(ctx,
		"SELECT count(*) FROM incidents WHERE occurred_at >= $1",
		since,
	).Scan(&count)
	return count, err
}

// GetThresholds reads the safety thresholds from the config table.
func (s *PostgresStore) GetThresholds(ctx context.Context) (*models.Thresholds, error) {
	var raw []byte
	err := s.pool.QueryRow(ctx, "SELECT value FROM config WHERE key = 'thresholds'").Scan(&raw)
	if err != nil {
		return nil, fmt.Errorf("postgres get thresholds: %w", err)
	}
	var t models.Thresholds
	if err := json.Unmarshal(raw, &t); err != nil {
		return nil, fmt.Errorf("unmarshal thresholds: %w", err)
	}
	return &t, nil
}

// UpdateThresholds writes updated safety thresholds to the config table.
func (s *PostgresStore) UpdateThresholds(ctx context.Context, t models.Thresholds) error {
	raw, err := json.Marshal(t)
	if err != nil {
		return fmt.Errorf("marshal thresholds: %w", err)
	}
	_, err = s.pool.Exec(ctx,
		"UPDATE config SET value = $1, updated_at = now() WHERE key = 'thresholds'",
		raw,
	)
	return err
}

// DeleteAllIncidents removes all incidents. Used by demo reset.
func (s *PostgresStore) DeleteAllIncidents(ctx context.Context) error {
	_, err := s.pool.Exec(ctx, "DELETE FROM incidents")
	return err
}

// Ping checks the PostgreSQL connection.
func (s *PostgresStore) Ping(ctx context.Context) error {
	return s.pool.Ping(ctx)
}

// ─── Platforms ────────────────────────────────────────────────────────────────

// GetPlatforms returns all robot platforms.
func (s *PostgresStore) GetPlatforms(ctx context.Context) ([]models.RobotPlatform, error) {
	rows, err := s.pool.Query(ctx,
		"SELECT id, name, manufacturer, category, sensors, default_thresholds, created_at FROM robot_platforms ORDER BY name")
	if err != nil {
		return nil, fmt.Errorf("postgres get platforms: %w", err)
	}
	defer rows.Close()

	var platforms []models.RobotPlatform
	for rows.Next() {
		var p models.RobotPlatform
		if err := rows.Scan(&p.ID, &p.Name, &p.Manufacturer, &p.Category, &p.Sensors, &p.DefaultThresholds, &p.CreatedAt); err != nil {
			return nil, fmt.Errorf("postgres scan platform: %w", err)
		}
		platforms = append(platforms, p)
	}
	return platforms, rows.Err()
}

// GetPlatform returns a single platform by ID.
func (s *PostgresStore) GetPlatform(ctx context.Context, id string) (*models.RobotPlatform, error) {
	var p models.RobotPlatform
	err := s.pool.QueryRow(ctx,
		"SELECT id, name, manufacturer, category, sensors, default_thresholds, created_at FROM robot_platforms WHERE id = $1", id,
	).Scan(&p.ID, &p.Name, &p.Manufacturer, &p.Category, &p.Sensors, &p.DefaultThresholds, &p.CreatedAt)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("postgres get platform: %w", err)
	}
	return &p, nil
}

// ─── Robots ──────────────────────────────────────────────────────────────────

// GetRobot returns a single robot by UUID (without platform join).
func (s *PostgresStore) GetRobot(ctx context.Context, id string) (*models.Robot, error) {
	const q = `SELECT id, platform_id, name, description,
		auth_token, ip_address, mac_address,
		status, thresholds, created_at
	FROM robots WHERE id = $1`

	var r models.Robot
	err := s.pool.QueryRow(ctx, q, id).Scan(
		&r.ID, &r.PlatformID, &r.Name, &r.Description,
		&r.AuthToken, &r.IPAddress, &r.MACAddress,
		&r.Status, &r.Thresholds, &r.CreatedAt,
	)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("postgres get robot: %w", err)
	}
	return &r, nil
}

// GetRobots returns all robots with their platform info.
func (s *PostgresStore) GetRobots(ctx context.Context) ([]models.Robot, error) {
	const q = `SELECT r.id, r.platform_id, r.name, r.description,
		r.auth_token, r.ip_address, r.mac_address,
		r.status, r.thresholds, r.created_at,
		p.id, p.name, p.manufacturer, p.category, p.sensors, p.default_thresholds, p.created_at
	FROM robots r JOIN robot_platforms p ON r.platform_id = p.id ORDER BY r.created_at`

	rows, err := s.pool.Query(ctx, q)
	if err != nil {
		return nil, fmt.Errorf("postgres get robots: %w", err)
	}
	defer rows.Close()

	var robots []models.Robot
	for rows.Next() {
		var r models.Robot
		var p models.RobotPlatform
		if err := rows.Scan(
			&r.ID, &r.PlatformID, &r.Name, &r.Description,
			&r.AuthToken, &r.IPAddress, &r.MACAddress,
			&r.Status, &r.Thresholds, &r.CreatedAt,
			&p.ID, &p.Name, &p.Manufacturer, &p.Category, &p.Sensors, &p.DefaultThresholds, &p.CreatedAt,
		); err != nil {
			return nil, fmt.Errorf("postgres scan robot: %w", err)
		}
		r.Platform = &p
		robots = append(robots, r)
	}
	return robots, rows.Err()
}

// CreateRobot inserts a new robot linked to a platform.
func (s *PostgresStore) CreateRobot(ctx context.Context, r models.Robot) (*models.Robot, error) {
	var created models.Robot
	err := s.pool.QueryRow(ctx,
		`INSERT INTO robots (platform_id, name, description, ip_address, mac_address)
		 VALUES ($1, $2, $3, $4, $5)
		 RETURNING id, platform_id, name, description, auth_token, ip_address, mac_address, status, thresholds, created_at`,
		r.PlatformID, r.Name, r.Description, r.IPAddress, r.MACAddress,
	).Scan(&created.ID, &created.PlatformID, &created.Name, &created.Description,
		&created.AuthToken, &created.IPAddress, &created.MACAddress,
		&created.Status, &created.Thresholds, &created.CreatedAt)
	if err != nil {
		return nil, fmt.Errorf("postgres create robot: %w", err)
	}
	return &created, nil
}

// DeleteRobot removes a robot by ID.
func (s *PostgresStore) DeleteRobot(ctx context.Context, id string) error {
	tag, err := s.pool.Exec(ctx, "DELETE FROM robots WHERE id = $1", id)
	if err != nil {
		return fmt.Errorf("postgres delete robot: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return fmt.Errorf("robot not found")
	}
	return nil
}

// UpdateRobotStatus updates a robot's status field.
func (s *PostgresStore) UpdateRobotStatus(ctx context.Context, name string, status string) error {
	_, err := s.pool.Exec(ctx, "UPDATE robots SET status = $1 WHERE name = $2", status, name)
	return err
}

// ─── Facilities ─────────────────────────────────────────────────────────────

// GetFacilities returns all facilities with computed robot counts.
func (s *PostgresStore) GetFacilities(ctx context.Context) ([]models.Facility, error) {
	const q = `SELECT f.id, f.name, COALESCE(f.description, ''), f.status, f.created_at,
		(SELECT count(*) FROM robots_registry rr WHERE rr.facility_id = f.id) AS robot_count,
		COALESCE(f.floor_plan_url, ''), COALESCE(f.floor_plan_width, 0), COALESCE(f.floor_plan_height, 0)
	FROM facilities f ORDER BY f.name`

	rows, err := s.pool.Query(ctx, q)
	if err != nil {
		return nil, fmt.Errorf("postgres get facilities: %w", err)
	}
	defer rows.Close()

	var facilities []models.Facility
	for rows.Next() {
		var f models.Facility
		if err := rows.Scan(&f.ID, &f.Name, &f.Description, &f.Status, &f.CreatedAt, &f.RobotCount,
			&f.FloorPlanURL, &f.FloorPlanWidth, &f.FloorPlanHeight); err != nil {
			return nil, fmt.Errorf("postgres scan facility: %w", err)
		}
		facilities = append(facilities, f)
	}
	return facilities, rows.Err()
}

// CreateFacility inserts a new facility.
func (s *PostgresStore) CreateFacility(ctx context.Context, id, name, description string) error {
	_, err := s.pool.Exec(ctx,
		`INSERT INTO facilities (id, name, description) VALUES ($1, $2, $3)`,
		id, name, description)
	return err
}

// UpdateFacility updates the name and description of a facility.
func (s *PostgresStore) UpdateFacility(ctx context.Context, id, name, description string) error {
	_, err := s.pool.Exec(ctx,
		`UPDATE facilities SET name = $2, description = $3 WHERE id = $1`,
		id, name, description)
	return err
}

// DeleteFacility removes a facility (cascades to floors → rooms).
func (s *PostgresStore) DeleteFacility(ctx context.Context, id string) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM facilities WHERE id = $1`, id)
	return err
}

// UpdateFacilityFloorPlan sets the floor plan URL and dimensions.
func (s *PostgresStore) UpdateFacilityFloorPlan(ctx context.Context, id, url string, width, height int) error {
	_, err := s.pool.Exec(ctx,
		`UPDATE facilities SET floor_plan_url = $2, floor_plan_width = $3, floor_plan_height = $4 WHERE id = $1`,
		id, url, width, height)
	return err
}

// ClearFacilityFloorPlan removes the floor plan from a facility.
func (s *PostgresStore) ClearFacilityFloorPlan(ctx context.Context, id string) error {
	_, err := s.pool.Exec(ctx,
		`UPDATE facilities SET floor_plan_url = NULL, floor_plan_width = NULL, floor_plan_height = NULL WHERE id = $1`,
		id)
	return err
}

// SaveCalibration upserts a floor plan calibration for a facility.
func (s *PostgresStore) SaveCalibration(ctx context.Context, c models.FloorPlanCalibration) error {
	const q = `INSERT INTO floor_plan_calibrations
		(facility_id, pixels_per_meter, origin_pixel_x, origin_pixel_y, origin_world_x, origin_world_y, rotation_rad, image_width, image_height)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		ON CONFLICT (facility_id) DO UPDATE SET
			pixels_per_meter = EXCLUDED.pixels_per_meter,
			origin_pixel_x = EXCLUDED.origin_pixel_x,
			origin_pixel_y = EXCLUDED.origin_pixel_y,
			origin_world_x = EXCLUDED.origin_world_x,
			origin_world_y = EXCLUDED.origin_world_y,
			rotation_rad = EXCLUDED.rotation_rad,
			image_width = EXCLUDED.image_width,
			image_height = EXCLUDED.image_height,
			calibrated_at = now()`
	_, err := s.pool.Exec(ctx, q,
		c.FacilityID, c.PixelsPerMeter, c.OriginPixelX, c.OriginPixelY,
		c.OriginWorldX, c.OriginWorldY, c.RotationRad, c.ImageWidth, c.ImageHeight)
	return err
}

// GetCalibration returns the floor plan calibration for a facility.
func (s *PostgresStore) GetCalibration(ctx context.Context, facilityID string) (*models.FloorPlanCalibration, error) {
	const q = `SELECT id, facility_id, pixels_per_meter, origin_pixel_x, origin_pixel_y,
		origin_world_x, origin_world_y, rotation_rad, image_width, image_height, calibrated_at
		FROM floor_plan_calibrations WHERE facility_id = $1`
	var c models.FloorPlanCalibration
	err := s.pool.QueryRow(ctx, q, facilityID).Scan(
		&c.ID, &c.FacilityID, &c.PixelsPerMeter, &c.OriginPixelX, &c.OriginPixelY,
		&c.OriginWorldX, &c.OriginWorldY, &c.RotationRad, &c.ImageWidth, &c.ImageHeight, &c.CalibratedAt)
	if err != nil {
		return nil, err
	}
	return &c, nil
}

// ─── Floors ────────────────────────────────────────────────────────────────

func (s *PostgresStore) GetFloors(ctx context.Context, facilityID string) ([]models.Floor, error) {
	const q = `SELECT id, facility_id, floor_number, name,
		COALESCE(floor_plan_url,''), COALESCE(floor_plan_width,0), COALESCE(floor_plan_height,0),
		COALESCE(status,'active'), created_at
		FROM floors WHERE facility_id = $1 ORDER BY floor_number`
	rows, err := s.pool.Query(ctx, q, facilityID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var floors []models.Floor
	for rows.Next() {
		var f models.Floor
		if err := rows.Scan(&f.ID, &f.FacilityID, &f.FloorNumber, &f.Name,
			&f.FloorPlanURL, &f.FloorPlanWidth, &f.FloorPlanHeight, &f.Status, &f.CreatedAt); err != nil {
			return nil, err
		}
		floors = append(floors, f)
	}
	return floors, nil
}

func (s *PostgresStore) GetFloor(ctx context.Context, floorID string) (*models.Floor, error) {
	const q = `SELECT id, facility_id, floor_number, name,
		COALESCE(floor_plan_url,''), COALESCE(floor_plan_width,0), COALESCE(floor_plan_height,0),
		COALESCE(status,'active'), created_at
		FROM floors WHERE id = $1`
	var f models.Floor
	err := s.pool.QueryRow(ctx, q, floorID).Scan(
		&f.ID, &f.FacilityID, &f.FloorNumber, &f.Name,
		&f.FloorPlanURL, &f.FloorPlanWidth, &f.FloorPlanHeight, &f.Status, &f.CreatedAt)
	if err != nil {
		return nil, err
	}
	return &f, nil
}

func (s *PostgresStore) CreateFloor(ctx context.Context, f models.Floor) (*models.Floor, error) {
	const q = `INSERT INTO floors (facility_id, floor_number, name)
		VALUES ($1, $2, $3) RETURNING id, created_at`
	err := s.pool.QueryRow(ctx, q, f.FacilityID, f.FloorNumber, f.Name).Scan(&f.ID, &f.CreatedAt)
	if err != nil {
		return nil, err
	}
	f.Status = "active"
	return &f, nil
}

func (s *PostgresStore) UpdateFloor(ctx context.Context, f models.Floor) error {
	const q = `UPDATE floors SET name = $2, floor_number = $3 WHERE id = $1`
	_, err := s.pool.Exec(ctx, q, f.ID, f.Name, f.FloorNumber)
	return err
}

func (s *PostgresStore) DeleteFloor(ctx context.Context, floorID string) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM floors WHERE id = $1`, floorID)
	return err
}

func (s *PostgresStore) UpdateFloorFloorPlan(ctx context.Context, floorID, url string, w, h int) error {
	const q = `UPDATE floors SET floor_plan_url = $2, floor_plan_width = $3, floor_plan_height = $4 WHERE id = $1`
	_, err := s.pool.Exec(ctx, q, floorID, url, w, h)
	return err
}

func (s *PostgresStore) ClearFloorFloorPlan(ctx context.Context, floorID string) error {
	const q = `UPDATE floors SET floor_plan_url = NULL, floor_plan_width = NULL, floor_plan_height = NULL WHERE id = $1`
	_, err := s.pool.Exec(ctx, q, floorID)
	return err
}

// SaveCalibrationByFloor upserts calibration data scoped to a specific floor.
func (s *PostgresStore) SaveCalibrationByFloor(ctx context.Context, c models.FloorPlanCalibration) error {
	const q = `INSERT INTO floor_plan_calibrations
		(facility_id, floor_id, pixels_per_meter, origin_pixel_x, origin_pixel_y, origin_world_x, origin_world_y, rotation_rad, image_width, image_height)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
		ON CONFLICT (floor_id) WHERE floor_id IS NOT NULL DO UPDATE SET
			pixels_per_meter = EXCLUDED.pixels_per_meter,
			origin_pixel_x = EXCLUDED.origin_pixel_x,
			origin_pixel_y = EXCLUDED.origin_pixel_y,
			origin_world_x = EXCLUDED.origin_world_x,
			origin_world_y = EXCLUDED.origin_world_y,
			rotation_rad = EXCLUDED.rotation_rad,
			image_width = EXCLUDED.image_width,
			image_height = EXCLUDED.image_height,
			calibrated_at = now()`
	_, err := s.pool.Exec(ctx, q,
		c.FacilityID, c.FloorID, c.PixelsPerMeter, c.OriginPixelX, c.OriginPixelY,
		c.OriginWorldX, c.OriginWorldY, c.RotationRad, c.ImageWidth, c.ImageHeight)
	return err
}

// GetCalibrationByFloor returns the floor plan calibration for a specific floor.
func (s *PostgresStore) GetCalibrationByFloor(ctx context.Context, floorID string) (*models.FloorPlanCalibration, error) {
	const q = `SELECT id, facility_id, COALESCE(floor_id::text,''), pixels_per_meter, origin_pixel_x, origin_pixel_y,
		origin_world_x, origin_world_y, rotation_rad, image_width, image_height, calibrated_at
		FROM floor_plan_calibrations WHERE floor_id = $1`
	var c models.FloorPlanCalibration
	err := s.pool.QueryRow(ctx, q, floorID).Scan(
		&c.ID, &c.FacilityID, &c.FloorID, &c.PixelsPerMeter, &c.OriginPixelX, &c.OriginPixelY,
		&c.OriginWorldX, &c.OriginWorldY, &c.RotationRad, &c.ImageWidth, &c.ImageHeight, &c.CalibratedAt)
	if err != nil {
		return nil, err
	}
	return &c, nil
}

// ─── Rooms ─────────────────────────────────────────────────────────────────

func (s *PostgresStore) GetRooms(ctx context.Context, floorID string) ([]models.Room, error) {
	const q = `SELECT id, floor_id, label, COALESCE(room_type,'general'), polygon_points, COALESCE(color,''), created_at
		FROM rooms WHERE floor_id = $1 ORDER BY label`
	rows, err := s.pool.Query(ctx, q, floorID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var rooms []models.Room
	for rows.Next() {
		var r models.Room
		if err := rows.Scan(&r.ID, &r.FloorID, &r.Label, &r.RoomType, &r.PolygonPoints, &r.Color, &r.CreatedAt); err != nil {
			return nil, err
		}
		rooms = append(rooms, r)
	}
	return rooms, nil
}

func (s *PostgresStore) CreateRoom(ctx context.Context, r models.Room) (*models.Room, error) {
	const q = `INSERT INTO rooms (floor_id, label, room_type, polygon_points, color)
		VALUES ($1, $2, $3, $4, $5) RETURNING id, created_at`
	err := s.pool.QueryRow(ctx, q, r.FloorID, r.Label, r.RoomType, r.PolygonPoints, r.Color).Scan(&r.ID, &r.CreatedAt)
	if err != nil {
		return nil, err
	}
	return &r, nil
}

func (s *PostgresStore) UpdateRoom(ctx context.Context, r models.Room) error {
	const q = `UPDATE rooms SET label = $2, room_type = $3, polygon_points = $4, color = $5 WHERE id = $1`
	_, err := s.pool.Exec(ctx, q, r.ID, r.Label, r.RoomType, r.PolygonPoints, r.Color)
	return err
}

// PartialUpdateRoom updates only the provided fields on a room.
func (s *PostgresStore) PartialUpdateRoom(ctx context.Context, roomID string, fields map[string]any) error {
	allowed := []string{"label", "room_type", "polygon_points", "color"}
	var sets []string
	var args []any
	args = append(args, roomID) // $1
	idx := 2
	for _, col := range allowed {
		if v, ok := fields[col]; ok {
			sets = append(sets, fmt.Sprintf("%s = $%d", col, idx))
			args = append(args, v)
			idx++
		}
	}
	if len(sets) == 0 {
		return nil
	}
	q := "UPDATE rooms SET " + strings.Join(sets, ", ") + " WHERE id = $1"
	_, err := s.pool.Exec(ctx, q, args...)
	return err
}

func (s *PostgresStore) DeleteRoom(ctx context.Context, roomID string) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM rooms WHERE id = $1`, roomID)
	return err
}

// ─── Robots Registry ────────────────────────────────────────────────────────

// GetRegistryRobots returns robots from the multi-vendor registry.
// If facilityID is non-empty, filters by facility.
func (s *PostgresStore) GetRegistryRobots(ctx context.Context, facilityID string) ([]models.RobotInfo, error) {
	var q string
	var args []any

	base := `SELECT robot_id, robot_type, vendor, COALESCE(facility_id, ''), COALESCE(floor_id::text, ''),
		COALESCE(room_ids, '[]'::jsonb),
		COALESCE(display_name, ''),
		COALESCE(ip_address, ''), COALESCE(ssh_username, ''), COALESCE(ssh_password, ''),
		status, last_seen_at
	FROM robots_registry`

	if facilityID != "" {
		q = base + ` WHERE facility_id = $1 ORDER BY robot_id`
		args = []any{facilityID}
	} else {
		q = base + ` ORDER BY robot_id`
	}

	rows, err := s.pool.Query(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("postgres get registry robots: %w", err)
	}
	defer rows.Close()

	var robots []models.RobotInfo
	for rows.Next() {
		var r models.RobotInfo
		if err := rows.Scan(&r.RobotID, &r.RobotType, &r.Vendor, &r.FacilityID, &r.FloorID,
			&r.RoomIDs, &r.DisplayName,
			&r.IPAddress, &r.SSHUsername, &r.SSHPassword, &r.Status, &r.LastSeenAt); err != nil {
			return nil, fmt.Errorf("postgres scan registry robot: %w", err)
		}
		robots = append(robots, r)
	}
	return robots, rows.Err()
}

// UpsertRegistryRobot inserts or updates a robot in the registry. Called on first connect.
func (s *PostgresStore) UpsertRegistryRobot(ctx context.Context, r models.RobotInfo) error {
	const q = `INSERT INTO robots_registry (robot_id, robot_type, vendor, facility_id, display_name, ip_address, ssh_username, ssh_password, status, last_seen_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
		ON CONFLICT (robot_id) DO UPDATE SET
			robot_type = EXCLUDED.robot_type,
			vendor = EXCLUDED.vendor,
			facility_id = COALESCE(NULLIF(EXCLUDED.facility_id, ''), robots_registry.facility_id),
			display_name = EXCLUDED.display_name,
			ip_address = COALESCE(NULLIF(EXCLUDED.ip_address, ''), robots_registry.ip_address),
			ssh_username = COALESCE(NULLIF(EXCLUDED.ssh_username, ''), robots_registry.ssh_username),
			ssh_password = COALESCE(NULLIF(EXCLUDED.ssh_password, ''), robots_registry.ssh_password),
			status = EXCLUDED.status,
			last_seen_at = now()`
	// Convert empty facility_id to NULL to satisfy the foreign key constraint.
	var facilityID interface{} = r.FacilityID
	if r.FacilityID == "" {
		facilityID = nil
	}
	_, err := s.pool.Exec(ctx, q, r.RobotID, r.RobotType, r.Vendor, facilityID, r.DisplayName,
		r.IPAddress, r.SSHUsername, r.SSHPassword, r.Status)
	return err
}

// UpdateRegistryRobotSeen updates last_seen_at for a robot.
func (s *PostgresStore) UpdateRegistryRobotSeen(ctx context.Context, robotID string) error {
	_, err := s.pool.Exec(ctx, "UPDATE robots_registry SET last_seen_at = now() WHERE robot_id = $1", robotID)
	return err
}

// UpdateRegistryRobotStatus updates a robot's status in the registry.
func (s *PostgresStore) UpdateRegistryRobotStatus(ctx context.Context, robotID, status string) error {
	_, err := s.pool.Exec(ctx, "UPDATE robots_registry SET status = $1, last_seen_at = now() WHERE robot_id = $2", status, robotID)
	return err
}

// UpdateRobotAssignment updates a robot's facility, floor, and room assignments.
func (s *PostgresStore) UpdateRobotAssignment(ctx context.Context, robotID, facilityID, floorID string, roomIDs json.RawMessage) error {
	var fid, flid interface{} = facilityID, floorID
	if facilityID == "" {
		fid = nil
	}
	if floorID == "" {
		flid = nil
	}
	if roomIDs == nil {
		roomIDs = json.RawMessage("[]")
	}
	const q = `UPDATE robots_registry SET facility_id = $2, floor_id = $3, room_ids = $4 WHERE robot_id = $1`
	_, err := s.pool.Exec(ctx, q, robotID, fid, flid, roomIDs)
	return err
}

// CreateIncidentMultiRobot inserts an incident with multi-robot metadata.
func (s *PostgresStore) CreateIncidentMultiRobot(ctx context.Context, e models.ClassifiedEvent, robotType, vendor, facilityID string) error {
	const q = `INSERT INTO incidents (
		id, robot_id, event_type, severity, description,
		distance_cm, speed, steering_angle, occurred_at,
		robot_type, vendor, facility_id
	) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)`

	_, err := s.pool.Exec(ctx, q,
		e.ID, e.RobotID, e.EventType, e.Severity, e.Description,
		e.DistanceCm, e.Speed, e.SteeringAngle, e.OccurredAt,
		robotType, vendor, facilityID,
	)
	return err
}
