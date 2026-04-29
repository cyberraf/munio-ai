package api

import (
	"encoding/json"
	"log"
	"net"
	"net/http"
	"time"

	"github.com/ai-security-brain/asb-core/internal/discovery"
	"github.com/ai-security-brain/asb-core/internal/models"
)

type scanRequest struct {
	Subnet string `json:"subnet"`
}

func handleDiscoveryScan(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req scanRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Subnet == "" {
			// Auto-detect subnet.
			subnet, err := discovery.GetLocalSubnet()
			if err != nil {
				writeError(w, http.StatusInternalServerError, "could not detect local subnet")
				return
			}
			req.Subnet = subnet
		}

		log.Printf("[discovery] scanning %s", req.Subnet)
		start := time.Now()
		devices, err := discovery.ScanNetwork(req.Subnet, 500*time.Millisecond)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		dur := time.Since(start).Milliseconds()
		log.Printf("[discovery] scan complete: %d devices found in %dms", len(devices), dur)

		writeJSON(w, http.StatusOK, map[string]any{
			"devices":          devices,
			"scan_duration_ms": dur,
			"subnet":           req.Subnet,
		})
	}
}

func handleDiscoveryDeploy(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var cfg discovery.DeployConfig
		if err := json.NewDecoder(r.Body).Decode(&cfg); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		if cfg.RobotIP == "" || cfg.SSHUsername == "" || cfg.SSHPassword == "" {
			writeError(w, http.StatusBadRequest, "robot_ip, ssh_username, and ssh_password are required")
			return
		}
		if cfg.RobotID == "" {
			writeError(w, http.StatusBadRequest, "robot_id is required")
			return
		}

		// Fill backend-side config.
		// Use the server's LAN IP so the robot can connect back, not "localhost".
		cfg.BackendHost = getServerLANIP()
		cfg.RobotAPIKey = deps.Auth.RobotAPIKey

		log.Printf("[discovery] deploying agent to %s@%s (robot_id=%s, type=%s, backend=%s)",
			cfg.SSHUsername, cfg.RobotIP, cfg.RobotID, cfg.RobotType, cfg.BackendHost)

		result, err := discovery.DeployAgent(cfg)
		if err != nil {
			log.Printf("[discovery] deploy failed: %v", err)
		}
		if result == nil {
			writeError(w, http.StatusInternalServerError, "deployment failed")
			return
		}

		// Register robot in the database so it appears in the fleet view.
		if result.Success {
			regErr := deps.Postgres.UpsertRegistryRobot(r.Context(), models.RobotInfo{
				RobotID:     cfg.RobotID,
				RobotType:   cfg.RobotType,
				Vendor:      vendorFromType(cfg.RobotType),
				FacilityID:  cfg.FacilityID,
				DisplayName: cfg.DisplayName,
				IPAddress:   cfg.RobotIP,
				SSHUsername:  cfg.SSHUsername,
				SSHPassword:  cfg.SSHPassword,
				Status:      "active",
			})
			if regErr != nil {
				log.Printf("[discovery] failed to register robot in database: %v", regErr)
			} else {
				log.Printf("[discovery] registered %s in robots_registry", cfg.RobotID)
			}
		}

		writeJSON(w, http.StatusOK, result)
	}
}

func vendorFromType(robotType string) string {
	switch robotType {
	case "picarx":
		return "sunfounder"
	case "turtlebot4", "ros2_generic":
		return "ros2"
	case "locus_origin":
		return "locus"
	default:
		return "custom"
	}
}

// getServerLANIP returns the server's non-loopback IPv4 address so the robot
// agent can connect back via WebSocket.
func getServerLANIP() string {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return "localhost"
	}
	for _, addr := range addrs {
		ipNet, ok := addr.(*net.IPNet)
		if !ok || ipNet.IP.IsLoopback() {
			continue
		}
		ip4 := ipNet.IP.To4()
		if ip4 == nil {
			continue
		}
		// Skip link-local
		if ip4[0] == 169 && ip4[1] == 254 {
			continue
		}
		return ip4.String()
	}
	return "localhost"
}

type sshRequest struct {
	RobotIP     string `json:"robot_ip"`
	SSHUsername  string `json:"ssh_username"`
	SSHPassword string `json:"ssh_password"`
}

func handleDiscoveryCheck(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req sshRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON")
			return
		}
		status, err := discovery.CheckAgentStatus(req.RobotIP, req.SSHUsername, req.SSHPassword)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": status})
	}
}

func handleDiscoveryStop(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req sshRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON")
			return
		}
		if err := discovery.StopAgent(req.RobotIP, req.SSHUsername, req.SSHPassword); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "stopped"})
	}
}

func handleDiscoveryRestart(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req sshRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON")
			return
		}
		if err := discovery.RestartAgent(req.RobotIP, req.SSHUsername, req.SSHPassword); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "running"})
	}
}

func handleDiscoveryUninstall(deps Dependencies) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req sshRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON")
			return
		}
		if err := discovery.UninstallAgent(req.RobotIP, req.SSHUsername, req.SSHPassword); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "removed"})
	}
}
