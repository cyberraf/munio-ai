package discovery

import (
	"fmt"
	"log"
	"strings"
	"time"

	"golang.org/x/crypto/ssh"
)

// DeployConfig holds the parameters for deploying a telemetry agent.
type DeployConfig struct {
	RobotIP     string `json:"robot_ip"`
	SSHUsername  string `json:"ssh_username"`
	SSHPassword string `json:"ssh_password"`
	RobotID     string `json:"robot_id"`
	RobotType   string `json:"robot_type"`
	FacilityID  string `json:"facility_id"`
	DisplayName string `json:"display_name"`
	BackendHost string `json:"-"` // filled by the server (its own address)
	RobotAPIKey string `json:"-"` // filled from auth config
}

// DeployResult describes the outcome of a deployment attempt.
type DeployResult struct {
	Success     bool   `json:"success"`
	Message     string `json:"message"`
	RobotID     string `json:"robot_id"`
	AgentStatus string `json:"agent_status"`
}

// DeployAgent SSHs into a robot and installs + starts the telemetry agent.
func DeployAgent(cfg DeployConfig) (*DeployResult, error) {
	client, err := sshConnect(cfg.RobotIP, cfg.SSHUsername, cfg.SSHPassword)
	if err != nil {
		return &DeployResult{Success: false, Message: fmt.Sprintf("SSH connection failed: %v", err), RobotID: cfg.RobotID}, err
	}
	defer client.Close()

	log.Printf("[deploy] connected to %s@%s", cfg.SSHUsername, cfg.RobotIP)

	// 1. Create agent directory.
	if _, err := runCmd(client, "mkdir -p ~/asb-agent"); err != nil {
		return fail(cfg.RobotID, "failed to create directory", err)
	}

	// 2. Detect robot type if not specified.
	if cfg.RobotType == "" || cfg.RobotType == "auto" {
		cfg.RobotType = detectRobotType(client)
		log.Printf("[deploy] detected robot type: %s", cfg.RobotType)
	}

	// 3. Generate and write the agent script.
	script := generateAgentScript(cfg)
	escaped := strings.ReplaceAll(script, "'", "'\\''")
	if _, err := runCmd(client, fmt.Sprintf("cat > ~/asb-agent/telemetry_agent.py << 'AGENT_EOF'\n%s\nAGENT_EOF", escaped)); err != nil {
		return fail(cfg.RobotID, "failed to write agent script", err)
	}
	log.Printf("[deploy] agent script written")

	// 4. Install dependencies.
	out, _ := runCmd(client, "pip3 show websockets 2>/dev/null | head -1")
	if !strings.Contains(out, "Name: websockets") {
		log.Printf("[deploy] installing websockets...")
		if _, err := runCmd(client, "pip3 install websockets --break-system-packages 2>&1"); err != nil {
			log.Printf("[deploy] pip install warning (non-fatal): %v", err)
		}
	}

	// 5. Stop any existing agent.
	runCmd(client, "sudo systemctl stop asb-agent.service 2>/dev/null; sudo systemctl disable asb-agent.service 2>/dev/null")
	runCmd(client, "pkill -f 'python3.*telemetry_agent' 2>/dev/null")

	// 6. Set up crontab for auto-start on reboot and start the agent now.
	cronLine := fmt.Sprintf("@reboot sleep 5 && /usr/bin/python3 /home/%s/asb-agent/telemetry_agent.py >> /home/%s/asb-agent/agent.log 2>&1 &", cfg.SSHUsername, cfg.SSHUsername)
	// Remove old entry and add new one
	cronCmd := fmt.Sprintf("(crontab -l 2>/dev/null | grep -v asb-agent; echo '%s') | crontab -", cronLine)
	if _, err := runCmd(client, cronCmd); err != nil {
		log.Printf("[deploy] crontab warning (non-fatal): %v", err)
	}

	// Start the agent immediately via nohup (gives full user environment for hardware access).
	startCmd := fmt.Sprintf("nohup /usr/bin/python3 /home/%s/asb-agent/telemetry_agent.py >> /home/%s/asb-agent/agent.log 2>&1 &", cfg.SSHUsername, cfg.SSHUsername)
	runCmd(client, startCmd)
	time.Sleep(3 * time.Second)

	// 7. Verify.
	out, _ = runCmd(client, "pgrep -f 'python3.*telemetry_agent' >/dev/null && echo active || echo inactive")
	status := strings.TrimSpace(out)
	log.Printf("[deploy] agent status: %s", status)

	return &DeployResult{
		Success:     status == "active",
		Message:     fmt.Sprintf("Agent deployed to %s (%s)", cfg.RobotIP, cfg.DisplayName),
		RobotID:     cfg.RobotID,
		AgentStatus: status,
	}, nil
}

// CheckAgentStatus returns the systemd service status of the agent.
func CheckAgentStatus(ip, username, password string) (string, error) {
	client, err := sshConnect(ip, username, password)
	if err != nil {
		return "", err
	}
	defer client.Close()
	out, err := runCmd(client, "systemctl is-active asb-agent.service 2>/dev/null || echo not_installed")
	return strings.TrimSpace(out), err
}

// StopAgent stops the telemetry agent service.
func StopAgent(ip, username, password string) error {
	client, err := sshConnect(ip, username, password)
	if err != nil {
		return err
	}
	defer client.Close()
	_, err = runCmd(client, "sudo systemctl stop asb-agent.service")
	return err
}

// RestartAgent restarts the telemetry agent service.
func RestartAgent(ip, username, password string) error {
	client, err := sshConnect(ip, username, password)
	if err != nil {
		return err
	}
	defer client.Close()
	_, err = runCmd(client, "sudo systemctl restart asb-agent.service")
	return err
}

// UninstallAgent stops, disables, and removes the agent.
func UninstallAgent(ip, username, password string) error {
	client, err := sshConnect(ip, username, password)
	if err != nil {
		return err
	}
	defer client.Close()
	cmds := []string{
		"sudo systemctl stop asb-agent.service 2>/dev/null",
		"sudo systemctl disable asb-agent.service 2>/dev/null",
		"sudo rm -f /etc/systemd/system/asb-agent.service",
		"sudo systemctl daemon-reload",
		"rm -rf ~/asb-agent",
	}
	for _, cmd := range cmds {
		runCmd(client, cmd) // best-effort
	}
	return nil
}

// ─── SSH helpers ────────────────────────────────────────────────────────────

func sshConnect(host, user, password string) (*ssh.Client, error) {
	config := &ssh.ClientConfig{
		User:            user,
		Auth:            []ssh.AuthMethod{ssh.Password(password)},
		HostKeyCallback: ssh.InsecureIgnoreHostKey(),
		Timeout:         10 * time.Second,
	}
	return ssh.Dial("tcp", host+":22", config)
}

func runCmd(client *ssh.Client, cmd string) (string, error) {
	session, err := client.NewSession()
	if err != nil {
		return "", err
	}
	defer session.Close()
	out, err := session.CombinedOutput(cmd)
	return string(out), err
}

func fail(robotID, msg string, err error) (*DeployResult, error) {
	return &DeployResult{
		Success:     false,
		Message:     fmt.Sprintf("%s: %v", msg, err),
		RobotID:     robotID,
		AgentStatus: "failed",
	}, err
}

// ─── Robot type detection ───────────────────────────────────────────────────

func detectRobotType(client *ssh.Client) string {
	// Check for PiCar-X.
	if out, _ := runCmd(client, "python3 -c 'import picarx' 2>&1"); !strings.Contains(out, "Error") && !strings.Contains(out, "No module") {
		return "picarx"
	}
	// Check for ROS 2.
	if out, _ := runCmd(client, "which ros2 2>/dev/null"); strings.TrimSpace(out) != "" {
		// Check for TurtleBot 4 specifically.
		if out2, _ := runCmd(client, "dpkg -l | grep turtlebot4 2>/dev/null"); strings.Contains(out2, "turtlebot4") {
			return "turtlebot4"
		}
		return "ros2_generic"
	}
	return "generic"
}

// ─── Agent script generation ────────────────────────────────────────────────

func generateAgentScript(cfg DeployConfig) string {
	wsURL := fmt.Sprintf("ws://%s:8080/ws/telemetry?key=%s", cfg.BackendHost, cfg.RobotAPIKey)

	switch cfg.RobotType {
	case "picarx":
		return generatePiCarXAgent(wsURL, cfg.RobotID, cfg.FacilityID)
	case "turtlebot4", "ros2_generic":
		return generateROS2Agent(wsURL, cfg.RobotID, cfg.RobotType, cfg.FacilityID)
	default:
		return generateGenericAgent(wsURL, cfg.RobotID, cfg.RobotType, cfg.FacilityID)
	}
}

func generatePiCarXAgent(wsURL, robotID, facilityID string) string {
	return fmt.Sprintf(`#!/usr/bin/env python3
"""ASB PiCar-X Telemetry Agent — auto-deployed"""
import asyncio, json, math, random, time, websockets
from collections import deque

WS_URL = "%s"
ROBOT_ID = "%s"
FACILITY_ID = "%s"

time.sleep(3)  # let I2C bus settle (Pi 5 RP1 needs this)
try:
    from picarx import Picarx
    from robot_hat import ADC, Pin, Ultrasonic
    px = Picarx()
    us = Ultrasonic(Pin("D2"), Pin("D3"))
    adc = ADC("A4")
    MOCK = False
except Exception as e:
    MOCK = True
    print(f"[agent] hardware not available ({e}) -- running mock mode")

buf = deque(maxlen=5)

def read():
    if MOCK:
        t = time.time()
        d = 47 + 32 * math.sin(t * 0.3) + random.gauss(0, 2)
        if random.random() < 0.03: d = random.uniform(5, 14)
        return {"distance_cm": round(d,1), "speed": round(random.gauss(0,2),1),
                "steering_angle": round(random.gauss(0,1),1), "battery_voltage": round(7.4+random.gauss(0,0.05),2),
                "camera_pan":0,"camera_tilt":0,"grayscale":{"left":800,"center":800,"right":800}}
    raw = us.read()
    buf.append(raw if raw > 0 else -1)
    dist = sum(v for v in buf if v > 0) / max(1, sum(1 for v in buf if v > 0)) if any(v > 0 for v in buf) else -1
    return {"distance_cm": round(dist,1), "speed": px.current_speed, "steering_angle": px.dir_current_angle,
            "camera_pan":0,"camera_tilt":0,"grayscale":{"left":0,"center":0,"right":0},
            "battery_voltage": round(adc.read_voltage(),2)}

# --- 2D Mapper ---
class PicarMapper:
    def __init__(self, grid_size_m=5.0, res_cm=5):
        self.gs=grid_size_m; self.res=res_cm/100.0; self.dim=int(grid_size_m/self.res)
        self.grid=[[0]*self.dim for _ in range(self.dim)]
        self.x=grid_size_m/2; self.y=grid_size_m/2; self.heading=0.0
        self.last_t=None; self.trail=[]; self.obs=[]; self._last_send=0
    def update(self,spd,steer_deg,dist_cm,ts_ms):
        t=ts_ms/1000.0
        if self.last_t is None: self.last_t=t; return
        dt=t-self.last_t; self.last_t=t
        if dt<=0 or dt>1: return
        v=abs(spd)*0.003; sr=math.radians(steer_deg)
        self.heading+=sr*v*2.0*dt; self.x+=v*math.cos(self.heading)*dt; self.y+=v*math.sin(self.heading)*dt
        if not self.trail or (ts_ms-self.trail[-1][2])>500:
            self.trail.append((self.x,self.y,ts_ms))
            if len(self.trail)>1000: self.trail.pop(0)
        if 0<dist_cm<400:
            dm=dist_cm/100.0; ox=self.x+dm*math.cos(self.heading); oy=self.y+dm*math.sin(self.heading)
            self.obs.append((ox,oy,ts_ms))
            if len(self.obs)>5000: self.obs.pop(0)
            steps=int(dm/self.res)
            for i in range(steps):
                f=i/max(steps,1); rx=self.x+f*dm*math.cos(self.heading); ry=self.y+f*dm*math.sin(self.heading)
                gx,gy=int(rx/self.res),int(ry/self.res)
                if 0<=gx<self.dim and 0<=gy<self.dim: self.grid[gy][gx]=1
            gx,gy=int(ox/self.res),int(oy/self.res)
            if 0<=gx<self.dim and 0<=gy<self.dim: self.grid[gy][gx]=2
    def should_send(self,ts_ms):
        if ts_ms-self._last_send>=2000: self._last_send=ts_ms; return True
        return False
    def get_data(self):
        flat=[];
        for r in self.grid: flat.extend(r)
        rle=[]; cur=flat[0]; cnt=1
        for v in flat[1:]:
            if v==cur: cnt+=1
            else: rle.append([cur,cnt]); cur=v; cnt=1
        rle.append([cur,cnt])
        return {"type":"map_update","grid_dim":self.dim,"resolution_cm":int(self.res*100),
                "grid_size_m":self.gs,"robot_x":round(self.x,3),"robot_y":round(self.y,3),
                "robot_heading":round(self.heading,3),
                "trail":[(round(x,2),round(y,2)) for x,y,_ in self.trail[-200:]],
                "obstacle_points":[(round(x,2),round(y,2)) for x,y,_ in self.obs[-2000:]],
                "grid_rle":rle}

mapper = PicarMapper()

async def main():
    backoff = 2
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                backoff = 2
                while True:
                    s = read()
                    ts = int(time.time()*1000)
                    s.update({"robot_id":ROBOT_ID,"facility_id":FACILITY_ID,"robot_type":"picarx","vendor":"sunfounder",
                              "timestamp_ms":ts,"status":"active","position_x":round(mapper.x,3),"position_y":round(mapper.y,3),"heading_deg":round(math.degrees(mapper.heading),1)})
                    await ws.send(json.dumps(s))
                    mapper.update(s.get("speed",0),s.get("steering_angle",0),s.get("distance_cm",-1),ts)
                    if mapper.should_send(ts):
                        md=mapper.get_data(); md["robot_id"]=ROBOT_ID
                        await ws.send(json.dumps(md))
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[agent] disconnected: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff*2, 30)

asyncio.run(main())
`, wsURL, robotID, facilityID)
}

func generateROS2Agent(wsURL, robotID, robotType, facilityID string) string {
	return fmt.Sprintf(`#!/usr/bin/env python3
"""ASB ROS 2 Telemetry Agent — auto-deployed"""
import asyncio, json, math, random, threading, time, websockets

WS_URL = "%s"
ROBOT_ID = "%s"
ROBOT_TYPE = "%s"
FACILITY_ID = "%s"

# Shared state
state = {"x":0,"y":0,"heading":0,"speed":0,"dist":999,"bat":12.0,"status":"active"}
lock = threading.Lock()

try:
    import rclpy
    from rclpy.node import Node
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import LaserScan, BatteryState
    import numpy as np

    class ASBNode(Node):
        def __init__(self):
            super().__init__("asb_agent")
            self.create_subscription(Odometry, "/odom", self._odom, 10)
            self.create_subscription(LaserScan, "/scan", self._scan, 10)
            self.create_subscription(BatteryState, "/battery_state", self._bat, 10)
        def _odom(self, m):
            o = m.pose.pose.orientation
            yaw = np.degrees(np.arctan2(2*(o.w*o.z+o.x*o.y), 1-2*(o.y*o.y+o.z*o.z)))
            spd = math.sqrt(m.twist.twist.linear.x**2+m.twist.twist.linear.y**2)
            with lock:
                state["x"]=m.pose.pose.position.x; state["y"]=m.pose.pose.position.y
                state["heading"]=yaw; state["speed"]=min(100,spd/1.0*100)
        def _scan(self, m):
            if not m.ranges: return
            arc = [r for r in m.ranges if 0.05<r<float("inf")]
            with lock: state["dist"]=min(arc)*100 if arc else 999
        def _bat(self, m):
            with lock: state["bat"]=m.voltage

    def spin():
        rclpy.init(); node=ASBNode(); rclpy.spin(node)

    threading.Thread(target=spin, daemon=True).start()
    MOCK = False
except Exception:
    MOCK = True
    def spin_mock():
        t=0
        while True:
            t+=0.05; a=t*0.1; r=3
            with lock:
                state["x"]=r*math.cos(a); state["y"]=r*math.sin(a)
                state["heading"]=math.degrees(a+math.pi/2)%%360
                state["speed"]=min(100,(0.35+0.15*math.sin(t*0.7))/1.0*100)
                state["dist"]=120+80*math.sin(t*0.2)+random.gauss(0,10)
                if random.random()<0.03: state["dist"]=random.uniform(8,25)
                state["bat"]=max(10,12.6-t*0.0002)
            time.sleep(0.05)
    threading.Thread(target=spin_mock, daemon=True).start()

async def main():
    backoff=2
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                backoff=2
                while True:
                    with lock: s=dict(state)
                    s.update({"robot_id":ROBOT_ID,"robot_type":ROBOT_TYPE,"vendor":"ros2","facility_id":FACILITY_ID,
                              "timestamp_ms":int(time.time()*1000),"steering_angle":0,"camera_pan":0,"camera_tilt":0,
                              "grayscale":{"left":0,"center":0,"right":0},"battery_voltage":s.pop("bat"),
                              "distance_cm":round(s.pop("dist"),1),"speed":round(s.pop("speed"),1),
                              "position_x":round(s.pop("x"),3),"position_y":round(s.pop("y"),3),
                              "heading_deg":round(s.pop("heading"),1),"status":s.pop("status")})
                    await ws.send(json.dumps(s))
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[agent] disconnected: {e}")
            await asyncio.sleep(backoff); backoff=min(backoff*2,30)

asyncio.run(main())
`, wsURL, robotID, robotType, facilityID)
}

func generateGenericAgent(wsURL, robotID, robotType, facilityID string) string {
	return fmt.Sprintf(`#!/usr/bin/env python3
"""ASB Generic Telemetry Agent — auto-deployed (mock mode)"""
import asyncio, json, math, random, time, websockets

WS_URL = "%s"
ROBOT_ID = "%s"

async def main():
    backoff=2
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                backoff=2; t=0
                while True:
                    t+=0.1
                    await ws.send(json.dumps({
                        "robot_id":ROBOT_ID,"robot_type":"%s","vendor":"custom","facility_id":"%s",
                        "timestamp_ms":int(time.time()*1000),"distance_cm":round(50+30*math.sin(t*0.3),1),
                        "speed":round(40+random.gauss(0,5),1),"steering_angle":0,"camera_pan":0,"camera_tilt":0,
                        "grayscale":{"left":0,"center":0,"right":0},"battery_voltage":round(7.4-t*0.0001,2),
                        "status":"active","position_x":0,"position_y":0,"heading_deg":0}))
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[agent] disconnected: {e}")
            await asyncio.sleep(backoff); backoff=min(backoff*2,30)

asyncio.run(main())
`, wsURL, robotID, robotType, facilityID)
}

func generateServiceUnit(cfg DeployConfig) string {
	return fmt.Sprintf(`[Unit]
Description=AI Security Brain Telemetry Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/su - %s -c "/usr/bin/python3 /home/%s/asb-agent/telemetry_agent.py"
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
`, cfg.SSHUsername, cfg.SSHUsername)
}
