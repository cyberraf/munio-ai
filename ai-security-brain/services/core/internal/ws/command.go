package ws

import (
	"encoding/json"
	"log"
	"net/http"
	"time"

	"github.com/gorilla/websocket"

	"github.com/ai-security-brain/asb-core/internal/models"
)

// CommandHandler receives commands from the dashboard and forwards them to the robot
// via the shared command channel.
type CommandHandler struct {
	cmdCh chan models.Command
}

// NewCommandHandler creates a CommandHandler that writes to the given command channel.
func NewCommandHandler(cmdCh chan models.Command) *CommandHandler {
	return &CommandHandler{cmdCh: cmdCh}
}

// ServeHTTP upgrades the connection and reads command messages.
func (h *CommandHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[command] upgrade error: %v", err)
		return
	}
	defer conn.Close()
	log.Println("[command] dashboard command client connected")

	conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				log.Printf("[command] read error: %v", err)
			}
			log.Println("[command] dashboard command client disconnected")
			return
		}

		var cmd models.Command
		if err := json.Unmarshal(msg, &cmd); err != nil {
			log.Printf("[command] invalid JSON: %v", err)
			continue
		}

		if cmd.Command != "estop" && cmd.Command != "resume" {
			log.Printf("[command] invalid command: %q", cmd.Command)
			conn.SetWriteDeadline(time.Now().Add(writeWait))
			conn.WriteJSON(map[string]string{"error": "invalid command, must be 'estop' or 'resume'"})
			continue
		}

		log.Printf("[command] received: %s", cmd.Command)

		select {
		case h.cmdCh <- cmd:
		default:
			log.Println("[command] command channel full, dropping")
		}
	}
}
