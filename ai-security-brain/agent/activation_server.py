#!/usr/bin/env python3
"""
ASB Activation Server
=====================
A lightweight HTTP server that runs on the robot (e.g. via systemd or rc.local)
and starts the telemetry agent when the ASB dashboard sends an activate command.

Environment variables
---------------------
AUTH_TOKEN        The robot's auth token (from the dashboard). Required.
ACTIVATION_PORT   Port to listen on. Default: 8765.
BACKEND_WS        WebSocket URL to pass to the agent. Default read from env.
ROBOT_ID          Robot name/ID to pass to the agent. Default read from env.

Usage
-----
    export AUTH_TOKEN="f731166f-664a-421e-a765-fb77ed527e53"
    export ROBOT_ID="picar-lab-01"
    export BACKEND_WS="ws://192.168.1.100:8080/ws/telemetry"
    python3 activation_server.py

    # In mock mode (no real PiCar-X hardware):
    export MOCK=1
    python3 activation_server.py
"""

import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [activation] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

AUTH_TOKEN      = os.environ.get("AUTH_TOKEN", "")
ACTIVATION_PORT = int(os.environ.get("ACTIVATION_PORT", "8765"))
AGENT_SCRIPT    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picar_telemetry_agent.py")
MOCK_MODE       = os.environ.get("MOCK", "0") not in ("0", "", "false", "False")

_agent_proc: subprocess.Popen | None = None
_lock = threading.Lock()


# ─── Agent lifecycle ──────────────────────────────────────────────────────────

def _agent_running() -> bool:
    return _agent_proc is not None and _agent_proc.poll() is None


def _start_agent() -> str:
    global _agent_proc
    with _lock:
        if _agent_running():
            log.info("activation requested but agent already running (PID %d)", _agent_proc.pid)
            return "already_running"

        env = os.environ.copy()
        cmd = [sys.executable, AGENT_SCRIPT]
        if MOCK_MODE:
            cmd.append("--mock")

        _agent_proc = subprocess.Popen(cmd, env=env)
        log.info("started agent (PID %d)", _agent_proc.pid)
        return "starting"


def _stop_agent() -> str:
    global _agent_proc
    with _lock:
        if not _agent_running():
            return "not_running"
        _agent_proc.terminate()
        try:
            _agent_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _agent_proc.kill()
        log.info("agent stopped")
        _agent_proc = None
        return "stopped"


# ─── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def _auth(self) -> bool:
        """Return True if the request is authorised."""
        if not AUTH_TOKEN:
            return True  # no token configured → open (warn at startup)
        return self.headers.get("X-Auth-Token", "") == AUTH_TOKEN

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/status":
            self._json(200, {
                "agent_running": _agent_running(),
                "pid": _agent_proc.pid if _agent_running() else None,
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return

        if self.path == "/activate":
            status = _start_agent()
            self._json(200, {"status": status})

        elif self.path == "/deactivate":
            status = _stop_agent()
            self._json(200, {"status": status})

        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args) -> None:  # suppress default CLF logs
        pass


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not AUTH_TOKEN:
        log.warning("AUTH_TOKEN not set — any request can activate the agent!")
    if MOCK_MODE:
        log.info("mock mode enabled")
    log.info("agent script: %s", AGENT_SCRIPT)

    server = HTTPServer(("0.0.0.0", ACTIVATION_PORT), Handler)
    log.info("listening on :%d", ACTIVATION_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        _stop_agent()
