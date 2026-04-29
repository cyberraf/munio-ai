package ws

import (
	"sync"
	"time"

	"github.com/ai-security-brain/asb-core/internal/models"
)

// MapStore holds in-memory map state for all robots, grouped by facility.
type MapStore struct {
	mu        sync.RWMutex
	robots    map[string]*models.RobotMapState // robot_id -> latest state
	snapshots map[string]*models.CameraSnapshot // robot_id -> latest snapshot
}

// NewMapStore creates an empty MapStore.
func NewMapStore() *MapStore {
	return &MapStore{
		robots:    make(map[string]*models.RobotMapState),
		snapshots: make(map[string]*models.CameraSnapshot),
	}
}

// UpdateMap stores the latest map data from a robot.
func (s *MapStore) UpdateMap(update models.MapUpdate) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.robots[update.RobotID] = &models.RobotMapState{
		RobotID:        update.RobotID,
		X:              update.RobotX,
		Y:              update.RobotY,
		Heading:        update.RobotHeading,
		Trail:          update.Trail,
		ObstaclePoints: update.ObstaclePoints,
		GridRLE:        update.GridRLE,
		GridDim:        update.GridDim,
		ResolutionCm:   update.ResolutionCm,
		GridSizeM:      update.GridSizeM,
		LastUpdate:     time.Now().UnixMilli(),
	}
}

// UpdateSnapshot stores the latest camera snapshot for a robot.
func (s *MapStore) UpdateSnapshot(snap models.CameraSnapshot) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.snapshots[snap.RobotID] = &snap
}

// GetFacilityMap returns a merged map for a facility.
// If facilityID is empty, returns all robots.
func (s *MapStore) GetFacilityMap(facilityID string, robotFacilities map[string]string) *models.FacilityMap {
	s.mu.RLock()
	defer s.mu.RUnlock()

	fm := &models.FacilityMap{
		FacilityID: facilityID,
		Robots:     make(map[string]models.RobotMapState),
	}

	for robotID, state := range s.robots {
		// Filter by facility if specified
		if facilityID != "" {
			if fid, ok := robotFacilities[robotID]; !ok || fid != facilityID {
				continue
			}
		}

		rs := *state
		// Attach latest snapshot URL if available
		if snap, ok := s.snapshots[robotID]; ok {
			rs.LatestSnapshot = snap.Image // base64 for now
		}
		fm.Robots[robotID] = rs

		if state.LastUpdate > fm.LastUpdate {
			fm.LastUpdate = state.LastUpdate
		}
	}

	return fm
}

// GetSnapshot returns the latest snapshot for a robot.
func (s *MapStore) GetSnapshot(robotID string) *models.CameraSnapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.snapshots[robotID]
}

// Reset clears all map data.
func (s *MapStore) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.robots = make(map[string]*models.RobotMapState)
	s.snapshots = make(map[string]*models.CameraSnapshot)
}
