package discovery

import (
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

// DiscoveredDevice represents a device found during network scan.
type DiscoveredDevice struct {
	IP         string `json:"ip"`
	Hostname   string `json:"hostname,omitempty"`
	SSHOpen    bool   `json:"ssh_open"`
	SSHBanner  string `json:"ssh_banner,omitempty"`
	OpenPorts  []int  `json:"open_ports"`
	DeviceType string `json:"device_type,omitempty"`
	IsRobot    bool   `json:"is_robot"`
}

// Common ports to probe on each device.
var probePorts = []int{22, 8883, 9090, 11311}

// ScanNetwork probes a CIDR subnet for reachable devices.
// It uses a worker pool of 50 goroutines for speed.
func ScanNetwork(subnet string, timeout time.Duration) ([]DiscoveredDevice, error) {
	if timeout == 0 {
		timeout = 500 * time.Millisecond
	}

	ips, err := expandCIDR(subnet)
	if err != nil {
		return nil, fmt.Errorf("invalid subnet %q: %w", subnet, err)
	}
	// Cap at 1024 IPs to prevent scanning huge subnets.
	if len(ips) > 1024 {
		ips = ips[:1024]
	}

	var (
		mu      sync.Mutex
		results []DiscoveredDevice
		wg      sync.WaitGroup
		sem     = make(chan struct{}, 50) // concurrency limit
	)

	for _, ip := range ips {
		wg.Add(1)
		sem <- struct{}{}
		go func(ip string) {
			defer wg.Done()
			defer func() { <-sem }()

			dev := probeDevice(ip, timeout)
			if dev != nil {
				mu.Lock()
				results = append(results, *dev)
				mu.Unlock()
			}
		}(ip)
	}
	wg.Wait()
	return results, nil
}

// GetLocalSubnet returns the CIDR of the primary non-loopback network interface.
func GetLocalSubnet() (string, error) {
	ifaces, err := net.Interfaces()
	if err != nil {
		return "", err
	}
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 || iface.Flags&net.FlagUp == 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			ipNet, ok := addr.(*net.IPNet)
			if !ok {
				continue
			}
			ip4 := ipNet.IP.To4()
			if ip4 == nil {
				continue
			}
			// Skip link-local (169.254.x.x) and APIPA addresses.
			if ip4[0] == 169 && ip4[1] == 254 {
				continue
			}
			// Skip subnets larger than /16 (too many hosts to scan).
			ones, _ := ipNet.Mask.Size()
			if ones < 16 {
				continue
			}
			// Prefer /24 or smaller private subnets.
			network := ip4.Mask(ipNet.Mask)
			return fmt.Sprintf("%s/%d", network.String(), ones), nil
		}
	}
	return "", fmt.Errorf("no suitable network interface found")
}

// probeDevice checks a single IP for open ports and SSH banner.
func probeDevice(ip string, timeout time.Duration) *DiscoveredDevice {
	var openPorts []int
	var sshBanner string
	sshOpen := false

	for _, port := range probePorts {
		addr := net.JoinHostPort(ip, fmt.Sprintf("%d", port))
		conn, err := net.DialTimeout("tcp", addr, timeout)
		if err != nil {
			continue
		}
		openPorts = append(openPorts, port)

		if port == 22 {
			sshOpen = true
			// Grab SSH banner (first line, up to 256 bytes).
			conn.SetReadDeadline(time.Now().Add(timeout))
			buf := make([]byte, 256)
			n, _ := conn.Read(buf)
			if n > 0 {
				sshBanner = strings.TrimSpace(string(buf[:n]))
			}
		}
		conn.Close()
	}

	if len(openPorts) == 0 {
		return nil
	}

	// Reverse DNS.
	hostname := ""
	names, err := net.LookupAddr(ip)
	if err == nil && len(names) > 0 {
		hostname = strings.TrimSuffix(names[0], ".")
	}

	deviceType, isRobot := classifyDevice(sshBanner, hostname, openPorts)

	return &DiscoveredDevice{
		IP:         ip,
		Hostname:   hostname,
		SSHOpen:    sshOpen,
		SSHBanner:  sshBanner,
		OpenPorts:  openPorts,
		DeviceType: deviceType,
		IsRobot:    isRobot,
	}
}

// classifyDevice uses heuristics on SSH banner, hostname, and open ports.
func classifyDevice(banner, hostname string, ports []int) (deviceType string, isRobot bool) {
	low := strings.ToLower(banner + " " + hostname)

	// ROS 2 ports indicate a robot.
	for _, p := range ports {
		if p == 9090 || p == 11311 {
			return "ros2_robot", true
		}
	}

	// Hostname hints.
	switch {
	case strings.Contains(low, "picar"):
		return "picarx", true
	case strings.Contains(low, "turtlebot"):
		return "turtlebot4", true
	case strings.Contains(low, "mir"):
		return "mir_robot", true
	case strings.Contains(low, "locus"):
		return "locus_robot", true
	}

	// SSH banner hints.
	if strings.Contains(low, "raspbian") || strings.Contains(low, "raspberry") ||
		(strings.Contains(low, "debian") && strings.Contains(low, "aarch64")) {
		return "raspberry_pi", false
	}
	if strings.Contains(low, "ubuntu") || strings.Contains(low, "debian") {
		return "linux", false
	}

	return "unknown", false
}

// expandCIDR returns all usable host IPs in a CIDR range, skipping .0 and .255.
func expandCIDR(cidr string) ([]string, error) {
	ip, ipNet, err := net.ParseCIDR(cidr)
	if err != nil {
		return nil, err
	}

	var ips []string
	for ip := ip.Mask(ipNet.Mask); ipNet.Contains(ip); incIP(ip) {
		// Skip network and broadcast addresses.
		last := ip[len(ip)-1]
		if last == 0 || last == 255 {
			continue
		}
		ips = append(ips, ip.String())
	}
	return ips, nil
}

func incIP(ip net.IP) {
	for j := len(ip) - 1; j >= 0; j-- {
		ip[j]++
		if ip[j] > 0 {
			break
		}
	}
}
