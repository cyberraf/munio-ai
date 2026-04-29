package classifier

import (
	"testing"
	"time"

	"github.com/ai-security-brain/asb-core/internal/models"
)

func defaultThresholds() models.Thresholds {
	return models.Thresholds{
		ProximityCm:      30,
		SpeedMax:         60,
		OffPathGrayscale: 1500,
		LowBatteryV:      6.0,
	}
}

func baseEvent(tsMs int64) models.TelemetryEvent {
	return models.TelemetryEvent{
		RobotID:        "picar-1",
		TimestampMs:    tsMs,
		DistanceCm:     50,
		Speed:          30,
		SteeringAngle:  0,
		CameraPan:      0,
		CameraTilt:     0,
		Grayscale:      models.Grayscale{Left: 500, Center: 500, Right: 500},
		BatteryVoltage: 7.4,
		Status:         "active",
	}
}

func TestProximityAlert_FiresAtDistance15(t *testing.T) {
	c := NewClassifier(defaultThresholds())
	e := baseEvent(1000)
	e.DistanceCm = 15

	events := c.Classify(e)
	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
	if events[0].EventType != models.EventProximityAlert {
		t.Errorf("expected %s, got %s", models.EventProximityAlert, events[0].EventType)
	}
	if events[0].Severity != models.SeverityHigh {
		t.Errorf("expected severity %s, got %s", models.SeverityHigh, events[0].Severity)
	}
}

func TestProximityAlert_DebounceWithin3Seconds(t *testing.T) {
	c := NewClassifier(defaultThresholds())
	base := time.Now().UnixMilli()

	e1 := baseEvent(base)
	e1.DistanceCm = 15
	events1 := c.Classify(e1)
	if len(events1) != 1 {
		t.Fatalf("first event: expected 1, got %d", len(events1))
	}

	// 2 seconds later — should be debounced
	e2 := baseEvent(base + 2000)
	e2.DistanceCm = 15
	events2 := c.Classify(e2)
	if len(events2) != 0 {
		t.Fatalf("debounce: expected 0 events, got %d", len(events2))
	}
}

func TestProximityAlert_FiresAgainAfterDebounce(t *testing.T) {
	c := NewClassifier(defaultThresholds())
	base := time.Now().UnixMilli()

	e1 := baseEvent(base)
	e1.DistanceCm = 15
	c.Classify(e1)

	// 4 seconds later — debounce expired
	e2 := baseEvent(base + 4000)
	e2.DistanceCm = 15
	events := c.Classify(e2)
	if len(events) != 1 {
		t.Fatalf("after debounce: expected 1 event, got %d", len(events))
	}
}

func TestSpeedViolation_Fires(t *testing.T) {
	c := NewClassifier(defaultThresholds())
	e := baseEvent(1000)
	e.Speed = 75
	e.DistanceCm = 50 // well above proximity threshold

	events := c.Classify(e)
	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
	if events[0].EventType != models.EventSpeedViolation {
		t.Errorf("expected %s, got %s", models.EventSpeedViolation, events[0].EventType)
	}
	if events[0].Severity != models.SeverityMedium {
		t.Errorf("expected severity %s, got %s", models.SeverityMedium, events[0].Severity)
	}
}

func TestEstop_FiresOnTransition(t *testing.T) {
	c := NewClassifier(defaultThresholds())
	base := time.Now().UnixMilli()

	// First event: active -> estop (should fire)
	e1 := baseEvent(base)
	e1.Status = "estop"
	events1 := c.Classify(e1)

	estopCount := 0
	for _, ev := range events1 {
		if ev.EventType == models.EventEstopTriggered {
			estopCount++
		}
	}
	if estopCount != 1 {
		t.Fatalf("transition: expected 1 estop event, got %d", estopCount)
	}

	// Second event: still estop (should NOT fire again)
	e2 := baseEvent(base + 1000)
	e2.Status = "estop"
	events2 := c.Classify(e2)

	for _, ev := range events2 {
		if ev.EventType == models.EventEstopTriggered {
			t.Fatal("estop should not fire on repeated estop status")
		}
	}
}

func TestNoEvents_WhenNormal(t *testing.T) {
	c := NewClassifier(defaultThresholds())
	e := baseEvent(1000)
	// All values are within normal ranges in baseEvent

	events := c.Classify(e)
	if len(events) != 0 {
		t.Fatalf("expected 0 events for normal telemetry, got %d", len(events))
	}
}
