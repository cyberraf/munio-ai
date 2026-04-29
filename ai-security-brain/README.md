# AI Security Brain

Behavioral security monitoring platform for autonomous robots. Real-time telemetry ingestion, rule-based safety classification, and a live dashboard for the PiCar-X.

## Architecture

```
PiCar-X (Raspberry Pi)          Laptop / Server
+-----------------------+        +-----------------------------------+
| picar_telemetry_agent |--WS--->| asb-core (Go)         :8080      |
| picar_autonomous      |        |   /ws/telemetry  ingest          |
+-----------------------+        |   /ws/live       broadcast       |
                                 |   /ws/alerts     broadcast       |
 Browser                         |   /ws/command    relay           |
+------------------------+       |   /api/*         REST endpoints  |
| Next.js Dashboard :3000|<-WS-->|                                  |
|                        |<-HTTP->|   classifier -> incidents       |
+------------------------+       +--------+--------+----------------+
                                          |        |
                                 +--------v--+ +---v--------+
                                 | ClickHouse | | PostgreSQL |
                                 | telemetry  | | incidents  |
                                 | :9000      | | config     |
                                 +------------+ | :5432      |
                                                +------------+
```

## Quick Start

### Prerequisites

- Docker Desktop
- Go 1.22+
- Node.js 18+
- Python 3.10+ (with `pip install websockets`)

### One-command start (Windows)

```powershell
.\start.ps1
```

This starts everything — databases, backend, mock agent, and dashboard — and prints URLs when ready. Press `Ctrl+C` to stop all services.

To stop separately:

```powershell
.\stop.ps1
```

### Step-by-step start

#### 1. Start the databases

```bash
docker compose up -d
```

This starts ClickHouse (ports 8123/9000) and PostgreSQL (port 5432) with schemas auto-initialized.

#### 2. Start the Go backend

```bash
cd services/core && go run ./cmd/server
```

The server starts on `:8080`. You should see:

```
[asb] clickhouse connected
[asb] postgres connected
[asb] classifier loaded (proximity=30cm, speed_max=60)
[asb] ASB Core running on :8080
```

#### 3. Start the mock telemetry agent

```bash
cd agent && python3 picar_telemetry_agent.py --mock
```

This generates synthetic sensor data at 10 Hz without needing the physical robot. You'll see the backend log incidents as they're classified.

#### 4. Start the dashboard

```bash
cd web/dashboard && npm run dev
```

Open http://localhost:3000. The dashboard connects via WebSocket and displays live telemetry, charts, alerts, and the incident log.

### All-in-one (4 terminals)

```bash
# Terminal 1 — databases
docker compose up -d

# Terminal 2 — backend
cd services/core && go run ./cmd/server

# Terminal 3 — mock agent
cd agent && python3 picar_telemetry_agent.py --mock

# Terminal 4 — dashboard
cd web/dashboard && npm run dev
```

## Running on the Real PiCar-X

On the Raspberry Pi:

```bash
# Edit the backend IP
nano agent/picar_telemetry_agent.py
# Change BACKEND_WS to your laptop's IP: ws://192.168.1.XXX:8080/ws/telemetry

# Terminal 1 — telemetry streaming
python3 agent/picar_telemetry_agent.py

# Terminal 2 — autonomous driving (optional)
python3 agent/picar_autonomous.py
```

The telemetry agent reads sensors only. The autonomous driver controls motors only. They can run simultaneously without conflict.

## Project Structure

```
ai-security-brain/
├── services/core/               # Go backend (single binary)
│   ├── cmd/server/main.go       # Entry point, wiring
│   ├── internal/
│   │   ├── api/                 # REST endpoints (chi router)
│   │   ├── ws/                  # WebSocket handlers
│   │   ├── classifier/          # Rule-based safety classifier
│   │   ├── store/               # ClickHouse + PostgreSQL clients
│   │   └── models/              # Shared data types
│   ├── Dockerfile
│   └── go.mod
├── web/dashboard/               # Next.js frontend
│   └── src/
│       ├── app/                 # App Router pages
│       ├── components/          # UI components
│       │   ├── telemetry/       # Gauges, indicators
│       │   ├── charts/          # Recharts visualizations
│       │   ├── alerts/          # Alert banner
│       │   ├── incidents/       # Incident log table
│       │   ├── controls/        # E-stop, thresholds, reset
│       │   ├── map/             # Position trail canvas
│       │   ├── layout/          # Header, shell
│       │   └── ui/              # shadcn/ui primitives
│       ├── hooks/               # useWebSocket, useTelemetry, useAlerts
│       └── lib/                 # API client, types
├── agent/                       # Python scripts for PiCar-X
│   ├── picar_telemetry_agent.py # Sensor streaming (10 Hz)
│   ├── picar_autonomous.py      # Obstacle avoidance demo
│   └── requirements.txt
├── init/                        # Database schemas
│   ├── clickhouse/001_schema.sql
│   └── postgres/001_schema.sql
├── scripts/                     # Test scripts
│   ├── test_e2e.sh              # Full pipeline E2E test
│   └── test_websocket.py        # WebSocket stream test
├── docker-compose.yml
├── Makefile
└── .gitignore
```

## API Reference

All endpoints are under `http://localhost:8080/api`.

| Method | Endpoint                | Description                           |
|--------|------------------------|---------------------------------------|
| GET    | /health                | Health check                          |
| GET    | /status                | System status (connection, uptime)    |
| GET    | /telemetry/latest      | Latest telemetry event (in-memory)    |
| GET    | /incidents             | List incidents (paginated, filterable)|
| GET    | /incidents/{id}        | Incident detail + telemetry context   |
| GET    | /metrics?range=1h      | Aggregated metrics (1h/6h/24h/7d)    |
| GET    | /config/thresholds     | Current safety thresholds             |
| POST   | /config/thresholds     | Update safety thresholds              |
| POST   | /demo/reset            | Clear all data (demo use)             |

### WebSocket Endpoints

| Endpoint       | Direction       | Description                        |
|----------------|-----------------|------------------------------------|
| /ws/telemetry  | Robot -> Server | Telemetry ingestion (10 Hz)        |
| /ws/live       | Server -> Dashboard | Live telemetry broadcast       |
| /ws/alerts     | Server -> Dashboard | Classified event broadcast     |
| /ws/command    | Dashboard -> Robot  | E-stop / resume commands       |

### Query Parameters

**GET /incidents**
- `limit` (default 50, max 200)
- `offset` (default 0)
- `type` — filter by event type (e.g., `PROXIMITY_ALERT`)
- `severity` — filter by severity (e.g., `HIGH`)

**GET /metrics**
- `range` — `1h`, `6h`, `24h`, or `7d` (default `1h`)

## Classification Rules

The classifier evaluates each telemetry event against configurable thresholds:

| Event              | Condition                              | Severity        | Debounce |
|--------------------|----------------------------------------|-----------------|----------|
| PROXIMITY_ALERT    | distance > 0 and < threshold           | CRITICAL/HIGH/MEDIUM | 3s  |
| SPEED_VIOLATION    | speed > speed_max                      | HIGH/MEDIUM     | 5s       |
| ESTOP_TRIGGERED    | status transitions to "estop"          | CRITICAL        | none     |
| PATH_DEVIATION     | all grayscale channels > threshold     | LOW             | 10s      |
| SENSOR_FAILURE     | distance <= 0 or battery < threshold   | HIGH/MEDIUM     | 30s      |

Default thresholds: proximity=30cm, speed_max=60, off_path_grayscale=1500, low_battery=6.0V

## Makefile Targets

```
make up             # Start databases (docker compose up -d)
make down           # Stop databases
make logs           # Tail database logs
make dev-core       # Run Go backend
make dev-dashboard  # Run Next.js dashboard
make agent          # Run telemetry agent (requires PiCar-X)
make agent-drive    # Run autonomous driver (requires PiCar-X)
make reset          # Reset demo data via API
make build          # Build Docker images
```

## Testing

```bash
# Run the full E2E pipeline test (starts everything, runs checks, cleans up)
bash scripts/test_e2e.sh

# Run the WebSocket stream test (requires backend + agent running)
python3 scripts/test_websocket.py

# Run Go unit tests
cd services/core && go test ./...
```

## Environment Variables

### Go Backend (services/core)

| Variable        | Default                                              |
|-----------------|------------------------------------------------------|
| PORT            | 8080                                                 |
| CLICKHOUSE_URL  | clickhouse://default:asb_dev@localhost:9000?database=default |
| POSTGRES_URL    | postgres://asb:asb_dev@localhost:5432/asb?sslmode=disable   |

### Next.js Dashboard (web/dashboard)

| Variable              | Default                      |
|-----------------------|------------------------------|
| NEXT_PUBLIC_API_URL   | http://localhost:8080/api    |
| NEXT_PUBLIC_WS_URL    | ws://localhost:8080          |

### Docker Compose

| Service     | Credentials        | Ports       |
|-------------|-------------------|-------------|
| ClickHouse  | default / asb_dev | 8123, 9000  |
| PostgreSQL  | asb / asb_dev     | 5432        |

## Tech Stack

- **Backend**: Go, chi router, gorilla/websocket, clickhouse-go, pgx
- **Frontend**: Next.js 16, TypeScript, Tailwind CSS, shadcn/ui, Recharts, TanStack Table
- **Databases**: ClickHouse (time-series telemetry), PostgreSQL (incidents, config)
- **Agent**: Python, asyncio, websockets
- **Infrastructure**: Docker Compose
