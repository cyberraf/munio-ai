package classifier

import (
	"fmt"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/ai-security-brain/asb-core/internal/models"
)

// Debounce durations per event type.
const (
	debounceProximity = 3 * time.Second
	debounceSpeed     = 5 * time.Second
	debouncePath      = 10 * time.Second
	debounceSensor    = 30 * time.Second
)

// Sensor failure sub-type keys for independent debounce tracking.
const (
	sensorUltrasonic = "SENSOR_FAILURE:ultrasonic"
	sensorBattery    = "SENSOR_FAILURE:battery"
)

// Classifier evaluates telemetry events against safety thresholds and produces classified events.
type Classifier struct {
	mu         sync.RWMutex
	thresholds models.Thresholds

	debounceMu sync.Mutex
	lastFired  map[string]time.Time
	prevStatus string
}

// NewClassifier creates a classifier with the given initial thresholds.
func NewClassifier(thresholds models.Thresholds) *Classifier {
	return &Classifier{
		thresholds: thresholds,
		lastFired:  make(map[string]time.Time),
	}
}

// UpdateThresholds replaces the current thresholds (thread-safe).
func (c *Classifier) UpdateThresholds(t models.Thresholds) {
	c.mu.Lock()
	c.thresholds = t
	c.mu.Unlock()
}

// Classify evaluates a single telemetry event and returns zero or more classified events.
func (c *Classifier) Classify(event models.TelemetryEvent) []models.ClassifiedEvent {
	c.mu.RLock()
	th := c.thresholds
	c.mu.RUnlock()

	now := event.Timestamp()
	var results []models.ClassifiedEvent

	// --- PROXIMITY_ALERT ---
	if event.DistanceCm > 0 && event.DistanceCm < th.ProximityCm {
		var severity string
		switch {
		case event.DistanceCm < 10:
			severity = models.SeverityCritical
		case event.DistanceCm < 20:
			severity = models.SeverityHigh
		default:
			severity = models.SeverityMedium
		}
		if c.debounceOK(models.EventProximityAlert, now, debounceProximity) {
			results = append(results, c.newEvent(event, now,
				models.EventProximityAlert, severity,
				fmt.Sprintf("Proximity alert: object detected at %.1fcm (threshold: %.0fcm)", event.DistanceCm, th.ProximityCm),
				"human_safety",
			))
		}
	}

	// --- SPEED_VIOLATION ---
	if event.Speed > th.SpeedMax {
		severity := models.SeverityMedium
		if event.Speed > th.SpeedMax*1.5 {
			severity = models.SeverityHigh
		}
		if c.debounceOK(models.EventSpeedViolation, now, debounceSpeed) {
			results = append(results, c.newEvent(event, now,
				models.EventSpeedViolation, severity,
				fmt.Sprintf("Speed violation: %.0f exceeds limit of %.0f", event.Speed, th.SpeedMax),
				"speed_control",
			))
		}
	}

	// --- ESTOP_TRIGGERED ---
	c.debounceMu.Lock()
	prevStatus := c.prevStatus
	c.prevStatus = event.Status
	c.debounceMu.Unlock()

	if event.Status == "estop" && prevStatus != "estop" {
		results = append(results, c.newEvent(event, now,
			models.EventEstopTriggered, models.SeverityCritical,
			"Emergency stop triggered",
			"emergency", "human_safety",
		))
	}

	// --- PATH_DEVIATION ---
	if event.Grayscale.Left > th.OffPathGrayscale &&
		event.Grayscale.Center > th.OffPathGrayscale &&
		event.Grayscale.Right > th.OffPathGrayscale {
		if c.debounceOK(models.EventPathDeviation, now, debouncePath) {
			results = append(results, c.newEvent(event, now,
				models.EventPathDeviation, models.SeverityLow,
				"Robot has deviated from designated path",
				"navigation_safety",
			))
		}
	}

	// --- SENSOR_FAILURE ---
	if event.DistanceCm <= 0 {
		if c.debounceOK(sensorUltrasonic, now, debounceSensor) {
			results = append(results, c.newEvent(event, now,
				models.EventSensorFailure, models.SeverityHigh,
				"Ultrasonic sensor failure: invalid distance reading",
				"sensor_integrity",
			))
		}
	}
	if event.BatteryVoltage > 0 && event.BatteryVoltage < th.LowBatteryV {
		if c.debounceOK(sensorBattery, now, debounceSensor) {
			results = append(results, c.newEvent(event, now,
				models.EventSensorFailure, models.SeverityMedium,
				fmt.Sprintf("Low battery: %.1fV below threshold %.1fV", event.BatteryVoltage, th.LowBatteryV),
				"sensor_integrity",
			))
		}
	}

	return results
}

// debounceOK returns true if enough time has elapsed since the last firing of the given key,
// and updates the last-fired timestamp.
func (c *Classifier) debounceOK(key string, now time.Time, dur time.Duration) bool {
	c.debounceMu.Lock()
	defer c.debounceMu.Unlock()

	if last, ok := c.lastFired[key]; ok && now.Sub(last) < dur {
		return false
	}
	c.lastFired[key] = now
	return true
}

// newEvent constructs a ClassifiedEvent from a telemetry event.
// extraTags are appended after the base vendor/robot_type tags.
func (c *Classifier) newEvent(e models.TelemetryEvent, t time.Time, eventType, severity, desc string, extraTags ...string) models.ClassifiedEvent {
	tags := make([]string, 0, 2+len(extraTags))
	if e.Vendor != "" {
		tags = append(tags, e.Vendor)
	}
	if e.RobotType != "" {
		tags = append(tags, e.RobotType)
	}
	tags = append(tags, extraTags...)

	return models.ClassifiedEvent{
		ID:            uuid.New().String(),
		RobotID:       e.RobotID,
		EventType:     eventType,
		Severity:      severity,
		Description:   desc,
		DistanceCm:    e.DistanceCm,
		Speed:         e.Speed,
		SteeringAngle: e.SteeringAngle,
		OccurredAt:    t,
		ComplianceRef: models.MapEventToCompliance(eventType, severity),
		Tags:          tags,
	}
}
