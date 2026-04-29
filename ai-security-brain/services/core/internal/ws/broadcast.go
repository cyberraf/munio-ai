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

const (
	broadcastWriteWait = 1 * time.Second
	clientSendBuffer   = 64 // buffered channel per client
)

// client wraps a websocket connection with a serialized send channel.
type client struct {
	conn *websocket.Conn
	send chan []byte
}

// writePump drains the send channel and writes to the websocket.
// One goroutine per client — no concurrent writes.
func (c *client) writePump() {
	defer c.conn.Close()
	for msg := range c.send {
		c.conn.SetWriteDeadline(time.Now().Add(broadcastWriteWait))
		if err := c.conn.WriteMessage(websocket.TextMessage, msg); err != nil {
			return
		}
	}
}

// Broadcaster manages dashboard WebSocket clients for live telemetry, alert, and map streams.
type Broadcaster struct {
	telemetryMu      sync.RWMutex
	telemetryClients map[*client]struct{}

	alertMu      sync.RWMutex
	alertClients map[*client]struct{}

	mapMu      sync.RWMutex
	mapClients map[*client]struct{}
}

// NewBroadcaster creates a Broadcaster with empty client sets.
func NewBroadcaster() *Broadcaster {
	return &Broadcaster{
		telemetryClients: make(map[*client]struct{}),
		alertClients:     make(map[*client]struct{}),
		mapClients:       make(map[*client]struct{}),
	}
}

// HandleLive upgrades the connection and registers it for live telemetry.
func (b *Broadcaster) HandleLive(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[broadcast] live upgrade error: %v", err)
		return
	}

	c := &client{conn: conn, send: make(chan []byte, clientSendBuffer)}
	go c.writePump()

	b.telemetryMu.Lock()
	b.telemetryClients[c] = struct{}{}
	b.telemetryMu.Unlock()
	log.Printf("[broadcast] live client connected (%d total)", b.telemetryCount())

	defer func() {
		b.telemetryMu.Lock()
		delete(b.telemetryClients, c)
		b.telemetryMu.Unlock()
		close(c.send)
		log.Printf("[broadcast] live client disconnected (%d total)", b.telemetryCount())
	}()

	conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})
	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			return
		}
	}
}

// HandleAlerts upgrades the connection and registers it for classified event alerts.
func (b *Broadcaster) HandleAlerts(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[broadcast] alerts upgrade error: %v", err)
		return
	}

	c := &client{conn: conn, send: make(chan []byte, clientSendBuffer)}
	go c.writePump()

	b.alertMu.Lock()
	b.alertClients[c] = struct{}{}
	b.alertMu.Unlock()
	log.Printf("[broadcast] alert client connected (%d total)", b.alertCount())

	defer func() {
		b.alertMu.Lock()
		delete(b.alertClients, c)
		b.alertMu.Unlock()
		close(c.send)
		log.Printf("[broadcast] alert client disconnected (%d total)", b.alertCount())
	}()

	conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})
	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			return
		}
	}
}

// BroadcastTelemetry sends a telemetry event to all live clients.
func (b *Broadcaster) BroadcastTelemetry(event models.TelemetryEvent) {
	data, err := json.Marshal(event)
	if err != nil {
		log.Printf("[broadcast] marshal telemetry error: %v", err)
		return
	}
	b.sendToClients(&b.telemetryMu, b.telemetryClients, data)
}

// BroadcastAlert sends a classified event to all alert clients.
func (b *Broadcaster) BroadcastAlert(event models.ClassifiedEvent) {
	data, err := json.Marshal(event)
	if err != nil {
		log.Printf("[broadcast] marshal alert error: %v", err)
		return
	}
	b.sendToClients(&b.alertMu, b.alertClients, data)
}

// sendToClients enqueues data to each client's send channel.
// Non-blocking: if a client's channel is full, the message is dropped (slow client).
func (b *Broadcaster) sendToClients(mu *sync.RWMutex, clients map[*client]struct{}, data []byte) {
	mu.RLock()
	defer mu.RUnlock()

	for c := range clients {
		select {
		case c.send <- data:
		default:
			// Client too slow, drop message
		}
	}
}

func (b *Broadcaster) telemetryCount() int {
	b.telemetryMu.RLock()
	defer b.telemetryMu.RUnlock()
	return len(b.telemetryClients)
}

func (b *Broadcaster) alertCount() int {
	b.alertMu.RLock()
	defer b.alertMu.RUnlock()
	return len(b.alertClients)
}

// HandleMap upgrades the connection and registers it for map updates.
func (b *Broadcaster) HandleMap(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[broadcast] map upgrade error: %v", err)
		return
	}

	c := &client{conn: conn, send: make(chan []byte, clientSendBuffer)}
	go c.writePump()

	b.mapMu.Lock()
	b.mapClients[c] = struct{}{}
	b.mapMu.Unlock()
	log.Printf("[broadcast] map client connected (%d total)", b.mapCount())

	defer func() {
		b.mapMu.Lock()
		delete(b.mapClients, c)
		b.mapMu.Unlock()
		close(c.send)
		log.Printf("[broadcast] map client disconnected (%d total)", b.mapCount())
	}()

	conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})
	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			return
		}
	}
}

// BroadcastMap sends a map update or camera snapshot to all map clients.
func (b *Broadcaster) BroadcastMap(data []byte) {
	b.sendToClients(&b.mapMu, b.mapClients, data)
}

func (b *Broadcaster) mapCount() int {
	b.mapMu.RLock()
	defer b.mapMu.RUnlock()
	return len(b.mapClients)
}
