package ws

import (
	"encoding/json"
	"log"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"github.com/ai-security-brain/asb-core/internal/models"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

const (
	readDeadline = 30 * time.Second
	pongWait     = 30 * time.Second
	pingInterval = 25 * time.Second
	writeWait    = 5 * time.Second
)

// robotConn tracks a single robot's WebSocket connection and its command channel.
type robotConn struct {
	conn  *websocket.Conn
	cmdCh chan models.Command
}

// IngestHandler manages robot WebSocket connections for telemetry ingestion
// and command forwarding. Multiple robots can connect simultaneously; only
// one connection per robot ID is allowed (a new connection replaces the old).
type IngestHandler struct {
	onTelemetry    func(models.TelemetryEvent)
	onMapUpdate    func(models.MapUpdate)
	onSnapshot     func(models.CameraSnapshot)
	onStatusChange func(robotId, status string) // called with "online"/"offline"
	broadcastCmdCh chan models.Command           // commands with empty RobotID go to all

	mu          sync.Mutex
	activeConns map[string]*robotConn // robot_id -> active conn + per-robot cmd channel
}

// IngestCallbacks holds all the callbacks for the IngestHandler.
type IngestCallbacks struct {
	OnTelemetry    func(models.TelemetryEvent)
	OnMapUpdate    func(models.MapUpdate)
	OnSnapshot     func(models.CameraSnapshot)
	OnStatusChange func(robotId, status string)
}

// NewIngestHandler creates an IngestHandler with the given callbacks and command channel.
func NewIngestHandler(cb IngestCallbacks, cmdCh chan models.Command) *IngestHandler {
	h := &IngestHandler{
		onTelemetry:    cb.OnTelemetry,
		onMapUpdate:    cb.OnMapUpdate,
		onSnapshot:     cb.OnSnapshot,
		onStatusChange: cb.OnStatusChange,
		broadcastCmdCh: cmdCh,
		activeConns:    make(map[string]*robotConn),
	}
	go h.routeCommands()
	return h
}

// routeCommands reads from broadcastCmdCh and dispatches to per-robot channels.
func (h *IngestHandler) routeCommands() {
	for cmd := range h.broadcastCmdCh {
		h.mu.Lock()
		if cmd.RobotID != "" {
			// Targeted command: send to specific robot.
			if rc, ok := h.activeConns[cmd.RobotID]; ok {
				select {
				case rc.cmdCh <- cmd:
				default:
					log.Printf("[ingest] command channel full for robot %q, dropping", cmd.RobotID)
				}
			} else {
				log.Printf("[ingest] command for unknown robot %q", cmd.RobotID)
			}
		} else {
			// Broadcast: send to all connected robots.
			for id, rc := range h.activeConns {
				select {
				case rc.cmdCh <- cmd:
				default:
					log.Printf("[ingest] command channel full for robot %q, dropping", id)
				}
			}
		}
		h.mu.Unlock()
	}
}

// IsRobotConnected returns whether at least one robot is currently connected.
func (h *IngestHandler) IsRobotConnected() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.activeConns) > 0
}

// ConnectedRobotCount returns how many robots are currently connected.
func (h *IngestHandler) ConnectedRobotCount() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.activeConns)
}

// ConnectedRobotIDs returns the IDs of all connected robots.
func (h *IngestHandler) ConnectedRobotIDs() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	ids := make([]string, 0, len(h.activeConns))
	for id := range h.activeConns {
		ids = append(ids, id)
	}
	return ids
}

// ServeHTTP upgrades the connection and begins the read/write loops.
// The robot identifies itself via the robot_id field in its first telemetry message.
// If another connection exists for the same robot_id, it is closed first.
func (h *IngestHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[ingest] upgrade error: %v", err)
		return
	}
	log.Println("[ingest] robot connecting (awaiting first telemetry)")

	// currentRobotID is set on the first valid telemetry message.
	var currentRobotID string
	var perRobotCmdCh chan models.Command

	defer func() {
		conn.Close()
		if currentRobotID != "" {
			h.mu.Lock()
			if rc, ok := h.activeConns[currentRobotID]; ok && rc.conn == conn {
				close(rc.cmdCh)
				delete(h.activeConns, currentRobotID)
				log.Printf("[ingest] robot %q disconnected (%d remaining)", currentRobotID, len(h.activeConns))
			}
			h.mu.Unlock()
			if h.onStatusChange != nil {
				h.onStatusChange(currentRobotID, "offline")
			}
		} else {
			log.Println("[ingest] connection closed before robot identified")
		}
	}()

	// Pong handler resets the read deadline.
	conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})

	// Writer goroutine: sends pings and forwards commands to this connection.
	done := make(chan struct{})
	go func() {
		ticker := time.NewTicker(pingInterval)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case cmd, ok := <-perRobotCmdCh:
				if !ok {
					return
				}
				conn.SetWriteDeadline(time.Now().Add(writeWait))
				if err := conn.WriteJSON(cmd); err != nil {
					log.Printf("[ingest] command write error: %v", err)
					return
				}
				log.Printf("[ingest] forwarded command %q to %s", cmd.Command, currentRobotID)
			case <-ticker.C:
				conn.SetWriteDeadline(time.Now().Add(writeWait))
				if err := conn.WriteMessage(websocket.PingMessage, nil); err != nil {
					return
				}
			}
		}
	}()

	// Create per-robot command channel (buffered so routing doesn't block).
	perRobotCmdCh = make(chan models.Command, 16)

	// Reader loop: reads telemetry, map update, and camera snapshot messages.
	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				log.Printf("[ingest] read error: %v", err)
			}
			close(done)
			return
		}

		conn.SetReadDeadline(time.Now().Add(readDeadline))

		// Peek at the "type" field to route the message.
		var peek struct {
			Type    string `json:"type"`
			RobotID string `json:"robot_id"`
		}
		if err := json.Unmarshal(msg, &peek); err != nil {
			log.Printf("[ingest] invalid JSON: %v", err)
			continue
		}

		// Register robot on first message of any type.
		if currentRobotID == "" && peek.RobotID != "" {
			currentRobotID = peek.RobotID
			h.mu.Lock()
			if old, exists := h.activeConns[currentRobotID]; exists && old.conn != conn {
				log.Printf("[ingest] replacing existing connection for robot %q", currentRobotID)
				close(old.cmdCh)
				old.conn.Close()
			}
			h.activeConns[currentRobotID] = &robotConn{conn: conn, cmdCh: perRobotCmdCh}
			count := len(h.activeConns)
			h.mu.Unlock()
			log.Printf("[ingest] robot %q online (%d connected)", currentRobotID, count)
			if h.onStatusChange != nil {
				h.onStatusChange(currentRobotID, "online")
			}
		}

		switch peek.Type {
		case "map_update":
			if h.onMapUpdate != nil {
				var mu models.MapUpdate
				if err := json.Unmarshal(msg, &mu); err == nil {
					h.onMapUpdate(mu)
				}
			}
		case "camera_snapshot":
			if h.onSnapshot != nil {
				var snap models.CameraSnapshot
				if err := json.Unmarshal(msg, &snap); err == nil {
					h.onSnapshot(snap)
				}
			}
		default:
			// Regular telemetry event.
			var event models.TelemetryEvent
			if err := json.Unmarshal(msg, &event); err != nil {
				continue
			}
			h.onTelemetry(event)
		}
	}
}
