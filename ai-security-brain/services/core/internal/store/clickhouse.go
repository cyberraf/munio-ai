package store

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "github.com/ClickHouse/clickhouse-go/v2"

	"github.com/ai-security-brain/asb-core/internal/models"
)

// ClickHouseStore wraps a database/sql connection to ClickHouse.
type ClickHouseStore struct {
	db *sql.DB
}

// NewClickHouseStore connects to ClickHouse via the native protocol and verifies with a ping.
func NewClickHouseStore(url string) (*ClickHouseStore, error) {
	db, err := sql.Open("clickhouse", url)
	if err != nil {
		return nil, fmt.Errorf("clickhouse open: %w", err)
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("clickhouse ping: %w", err)
	}
	return &ClickHouseStore{db: db}, nil
}

// Column list shared across write methods.
const telemetryCols = `robot_id, timestamp, distance_cm, speed, steering_angle,
	camera_pan, camera_tilt, grayscale_l, grayscale_c, grayscale_r,
	battery_voltage, status, robot_type, vendor, facility_id,
	position_x, position_y, heading_deg`

const telemetryPlaceholders = `?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?`

func telemetryArgs(e models.TelemetryEvent) []any {
	return []any{
		e.RobotID, e.Timestamp(), e.DistanceCm, e.Speed, e.SteeringAngle,
		e.CameraPan, e.CameraTilt, e.Grayscale.Left, e.Grayscale.Center, e.Grayscale.Right,
		e.BatteryVoltage, e.Status, e.RobotType, e.Vendor, e.FacilityID,
		e.PositionX, e.PositionY, e.HeadingDeg,
	}
}

func scanTelemetry(scanner interface{ Scan(dest ...any) error }) (models.TelemetryEvent, time.Time, error) {
	var e models.TelemetryEvent
	var ts time.Time
	err := scanner.Scan(
		&e.RobotID, &ts, &e.DistanceCm, &e.Speed, &e.SteeringAngle,
		&e.CameraPan, &e.CameraTilt, &e.Grayscale.Left, &e.Grayscale.Center, &e.Grayscale.Right,
		&e.BatteryVoltage, &e.Status, &e.RobotType, &e.Vendor, &e.FacilityID,
		&e.PositionX, &e.PositionY, &e.HeadingDeg,
	)
	return e, ts, err
}

// WriteTelemetry inserts a single telemetry event.
func (s *ClickHouseStore) WriteTelemetry(ctx context.Context, e models.TelemetryEvent) error {
	q := fmt.Sprintf("INSERT INTO telemetry (%s) VALUES (%s)", telemetryCols, telemetryPlaceholders)
	_, err := s.db.ExecContext(ctx, q, telemetryArgs(e)...)
	return err
}

// WriteTelemetryBatch inserts multiple telemetry events in a single batch.
func (s *ClickHouseStore) WriteTelemetryBatch(ctx context.Context, events []models.TelemetryEvent) error {
	if len(events) == 0 {
		return nil
	}

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("clickhouse begin tx: %w", err)
	}

	q := fmt.Sprintf("INSERT INTO telemetry (%s) VALUES (%s)", telemetryCols, telemetryPlaceholders)
	stmt, err := tx.PrepareContext(ctx, q)
	if err != nil {
		tx.Rollback()
		return fmt.Errorf("clickhouse prepare: %w", err)
	}
	defer stmt.Close()

	for _, e := range events {
		if _, err := stmt.ExecContext(ctx, telemetryArgs(e)...); err != nil {
			tx.Rollback()
			return fmt.Errorf("clickhouse batch exec: %w", err)
		}
	}

	return tx.Commit()
}

// GetLatestTelemetry returns the most recent telemetry event for a robot.
func (s *ClickHouseStore) GetLatestTelemetry(ctx context.Context, robotID string) (*models.TelemetryEvent, error) {
	q := fmt.Sprintf("SELECT %s FROM telemetry WHERE robot_id = ? ORDER BY timestamp DESC LIMIT 1", telemetryCols)

	e, ts, err := scanTelemetry(s.db.QueryRowContext(ctx, q, robotID))
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("clickhouse get latest: %w", err)
	}
	e.TimestampMs = ts.UnixMilli()
	return &e, nil
}

// GetTelemetryRange returns telemetry events within a time window for a robot.
func (s *ClickHouseStore) GetTelemetryRange(ctx context.Context, robotID string, start, end time.Time) ([]models.TelemetryEvent, error) {
	q := fmt.Sprintf("SELECT %s FROM telemetry WHERE robot_id = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp", telemetryCols)

	rows, err := s.db.QueryContext(ctx, q, robotID, start, end)
	if err != nil {
		return nil, fmt.Errorf("clickhouse get range: %w", err)
	}
	defer rows.Close()

	var events []models.TelemetryEvent
	for rows.Next() {
		e, ts, err := scanTelemetry(rows)
		if err != nil {
			return nil, fmt.Errorf("clickhouse scan range: %w", err)
		}
		e.TimestampMs = ts.UnixMilli()
		events = append(events, e)
	}
	return events, rows.Err()
}

// GetHourlyMetrics queries the telemetry_hourly_mv materialized view.
// If robotID is empty, aggregates across all robots.
// If facilityID is non-empty, filters by facility.
func (s *ClickHouseStore) GetHourlyMetrics(ctx context.Context, robotID string, since time.Time) ([]models.HourlyMetric, error) {
	var q string
	var args []any

	if robotID != "" {
		q = `SELECT hour, event_count, min_distance, avg_distance, avg_speed, max_speed
			FROM telemetry_hourly_mv WHERE robot_id = ? AND hour >= ? ORDER BY hour`
		args = []any{robotID, since}
	} else {
		q = `SELECT hour, sum(event_count), min(min_distance), avg(avg_distance), avg(avg_speed), max(max_speed)
			FROM telemetry_hourly_mv WHERE hour >= ? GROUP BY hour ORDER BY hour`
		args = []any{since}
	}

	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("clickhouse hourly metrics: %w", err)
	}
	defer rows.Close()

	var metrics []models.HourlyMetric
	for rows.Next() {
		var m models.HourlyMetric
		var hour time.Time
		if err := rows.Scan(
			&hour, &m.EventCount, &m.MinDistance, &m.AvgDistance, &m.AvgSpeed, &m.MaxSpeed,
		); err != nil {
			return nil, fmt.Errorf("clickhouse scan hourly: %w", err)
		}
		m.Hour = hour.UTC().Format(time.RFC3339)
		metrics = append(metrics, m)
	}
	return metrics, rows.Err()
}

// GetHourlyMetricsByFacility queries the facility-level materialized view.
func (s *ClickHouseStore) GetHourlyMetricsByFacility(ctx context.Context, facilityID string, since time.Time) ([]models.HourlyMetric, error) {
	q := `SELECT hour, event_count, min_distance, avg_distance, avg_speed, max_speed
		FROM telemetry_facility_hourly_mv WHERE facility_id = ? AND hour >= ? ORDER BY hour`

	rows, err := s.db.QueryContext(ctx, q, facilityID, since)
	if err != nil {
		return nil, fmt.Errorf("clickhouse facility hourly: %w", err)
	}
	defer rows.Close()

	var metrics []models.HourlyMetric
	for rows.Next() {
		var m models.HourlyMetric
		var hour time.Time
		if err := rows.Scan(&hour, &m.EventCount, &m.MinDistance, &m.AvgDistance, &m.AvgSpeed, &m.MaxSpeed); err != nil {
			return nil, fmt.Errorf("clickhouse scan facility hourly: %w", err)
		}
		m.Hour = hour.UTC().Format(time.RFC3339)
		metrics = append(metrics, m)
	}
	return metrics, rows.Err()
}

// GetOverallMetrics returns aggregate totals from raw telemetry.
// If robotID is empty, aggregates across all robots.
func (s *ClickHouseStore) GetOverallMetrics(ctx context.Context, robotID string, since time.Time) (totalEvents int64, avgSpeed float64, minDistance float64, err error) {
	var q string
	var args []any

	if robotID != "" {
		q = `SELECT
			count() AS total_events,
			if(count() > 0, avg(speed), 0) AS avg_speed,
			if(count() > 0, min(distance_cm), 0) AS min_distance
		FROM telemetry
		WHERE robot_id = ? AND timestamp >= ?`
		args = []any{robotID, since}
	} else {
		q = `SELECT
			count() AS total_events,
			if(count() > 0, avg(speed), 0) AS avg_speed,
			if(count() > 0, min(distance_cm), 0) AS min_distance
		FROM telemetry
		WHERE timestamp >= ?`
		args = []any{since}
	}

	err = s.db.QueryRowContext(ctx, q, args...).Scan(&totalEvents, &avgSpeed, &minDistance)
	if err != nil {
		err = fmt.Errorf("clickhouse overall metrics: %w", err)
	}
	return
}

// Truncate removes all data from the telemetry table. Used by demo reset.
func (s *ClickHouseStore) Truncate(ctx context.Context) error {
	_, err := s.db.ExecContext(ctx, "TRUNCATE TABLE telemetry")
	return err
}

// Ping checks the ClickHouse connection.
func (s *ClickHouseStore) Ping(ctx context.Context) error {
	return s.db.PingContext(ctx)
}
