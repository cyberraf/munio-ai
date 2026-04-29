# AI Security Brain — Start all services
# Usage: .\start.ps1
#   Starts databases, backend, mock agent, and dashboard.
#   Press Ctrl+C to stop everything.

$ErrorActionPreference = "Stop"

# ─── Resolve tool paths ─────────────────────────────────────────────────────
$extraPaths = @(
    "$env:ProgramFiles\Go\bin",
    "$env:ProgramFiles\nodejs",
    "$env:LocalAppData\Microsoft\WindowsApps"
)
foreach ($p in $extraPaths) {
    if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) {
        $env:PATH = "$p;$env:PATH"
    }
}

# Verify required tools
$missing = @()
if (-not (Get-Command go     -ErrorAction SilentlyContinue)) { $missing += "go (install from https://go.dev)" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { $missing += "docker (install Docker Desktop)" }
if (-not (Get-Command npm    -ErrorAction SilentlyContinue)) { $missing += "npm (install Node.js from https://nodejs.org)" }

# Find a Python interpreter that actually has the intelligence-engine deps
# installed. Several Python installs may be on PATH (Microsoft Store stub,
# system Python, etc.) — only the one with `uvicorn` installed is usable.
$pythonCmd = $null
$pythonCandidates = @(
    "C:\Python314\python.exe",
    "C:\Python313\python.exe",
    "py -3.14",
    "py -3.13",
    "py -3",
    "python3",
    "python"
)
foreach ($cand in $pythonCandidates) {
    $exe = ($cand -split " ")[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $check = & cmd /c "$cand -c ""import uvicorn"" >nul 2>&1 && echo OK"
    if ($check -match "OK") { $pythonCmd = $cand; break }
}
if (-not $pythonCmd) {
    $missing += "python with uvicorn installed (run: pip install -r services/intelligence/requirements.txt)"
}

if ($missing.Count -gt 0) {
    Write-Host "Missing required tools:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor Red }
    Write-Host "`nInstall them and restart your terminal, then try again." -ForegroundColor DarkGray
    exit 1
}

# ─── Kill leftover processes from a previous run ────────────────────────────
foreach ($port in @(8080, 8081, 3000)) {
    $lines = netstat -aon 2>$null | Select-String ":$port\s.*LISTEN"
    foreach ($line in $lines) {
        $procId = ($line -split '\s+')[-1]
        if ($procId -and $procId -ne "0") {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ""
Write-Host "  AI Security Brain - Starting all services" -ForegroundColor Cyan
Write-Host "  ===========================================" -ForegroundColor DarkGray
Write-Host ""

# Track child processes for cleanup
$childPIDs = @()

# ─── 1. Start databases ─────────────────────────────────────────────────────
Write-Host "[1/4] Starting databases..." -ForegroundColor Yellow
docker compose up -d
Start-Sleep -Seconds 5

Write-Host "       Waiting for ClickHouse..." -ForegroundColor DarkGray
for ($i = 0; $i -lt 30; $i++) {
    try {
        $result = (Invoke-WebRequest -Uri "http://localhost:8123/?user=default&password=asb_dev" -Method POST -Body "SELECT 1" -UseBasicParsing -ErrorAction SilentlyContinue).Content.Trim()
        if ($result -eq "1") { Write-Host "       ClickHouse ready" -ForegroundColor Green; break }
    } catch {}
    Start-Sleep -Seconds 1
}

Write-Host "       Waiting for PostgreSQL..." -ForegroundColor DarkGray
for ($i = 0; $i -lt 20; $i++) {
    docker compose exec -T postgres pg_isready -U asb 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "       PostgreSQL ready" -ForegroundColor Green; break }
    Start-Sleep -Seconds 1
}

# ─── 2. Start Go backend ────────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/4] Starting Go backend on :8080..." -ForegroundColor Yellow

# Use cmd /c to launch .cmd/.bat wrappers correctly. This works for all tools.
$coreDir = Join-Path $PWD "services\core"
$proc = Start-Process cmd -ArgumentList "/c","cd /d `"$coreDir`" && go run ./cmd/server" -PassThru -NoNewWindow
$childPIDs += $proc.Id
Start-Sleep -Seconds 4

try {
    $status = (Invoke-WebRequest -Uri "http://localhost:8080/api/status" -UseBasicParsing -ErrorAction SilentlyContinue).Content | ConvertFrom-Json
    Write-Host "       Backend running (db_status=$($status.db_status))" -ForegroundColor Green
} catch {
    Write-Host "       Backend may still be starting..." -ForegroundColor DarkYellow
}

# ─── 3. Start mock agents ───────────────────────────────────────────────────
Write-Host ""
Write-Host "[3/5] Starting mock telemetry agents..." -ForegroundColor Yellow

$agentDir = Join-Path $PWD "agent"
$wsBase = "ws://localhost:8080/ws/telemetry?key=asb-robot-key-2026"

# PiCar-X mock
$proc = Start-Process cmd -ArgumentList "/c","cd /d `"$agentDir`" && $pythonCmd picar_telemetry_agent.py --mock --url $wsBase --robot-id picar-1" -PassThru -NoNewWindow
$childPIDs += $proc.Id
Write-Host "       PiCar-X mock agent starting..." -ForegroundColor DarkGray

# TurtleBot mock
$proc = Start-Process cmd -ArgumentList "/c","cd /d `"$agentDir`" && $pythonCmd ros2_connector.py --mock --url $wsBase --robot-id turtlebot-001 --facility-id livingroom" -PassThru -NoNewWindow
$childPIDs += $proc.Id
Write-Host "       TurtleBot mock agent starting..." -ForegroundColor DarkGray

# Locus fleet mock (3 robots)
$proc = Start-Process cmd -ArgumentList "/c","cd /d `"$agentDir`" && $pythonCmd locus_connector.py --mock --url $wsBase --facility-id livingroom --robot-count 3" -PassThru -NoNewWindow
$childPIDs += $proc.Id
Write-Host "       Locus fleet mock (3 robots) starting..." -ForegroundColor DarkGray

Start-Sleep -Seconds 4

try {
    $status = (Invoke-WebRequest -Uri "http://localhost:8080/api/status" -UseBasicParsing).Content | ConvertFrom-Json
    if ($status.robot_connected) {
        Write-Host "       Agents connected (events=$($status.total_events))" -ForegroundColor Green
    } else {
        Write-Host "       Agents starting, waiting for connections..." -ForegroundColor DarkYellow
    }
} catch {}

# ─── 4. Start Intelligence Engine ───────────────────────────────────────────
Write-Host ""
Write-Host "[4/6] Starting Intelligence Engine on :8081..." -ForegroundColor Yellow

$intelDir = Join-Path $PWD "services\intelligence"
$proc = Start-Process cmd -ArgumentList "/c","cd /d `"$intelDir`" && $pythonCmd -m uvicorn app.main:app --host 0.0.0.0 --port 8081" -PassThru -NoNewWindow
$childPIDs += $proc.Id
Start-Sleep -Seconds 4

try {
    $result = (Invoke-WebRequest -Uri "http://localhost:8081/health" -UseBasicParsing -ErrorAction SilentlyContinue).Content | ConvertFrom-Json
    if ($result.status -eq "ok") {
        Write-Host "       Intelligence Engine running" -ForegroundColor Green
    }
} catch {
    Write-Host "       Intelligence Engine may still be starting (install deps: pip install -r services/intelligence/requirements.txt)" -ForegroundColor DarkYellow
}

# ─── 5. Start dashboard ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "[5/6] Starting dashboard on :3000..." -ForegroundColor Yellow

$dashDir = Join-Path $PWD "web\dashboard"
$proc = Start-Process cmd -ArgumentList "/c","cd /d `"$dashDir`" && npm run dev" -PassThru -NoNewWindow
$childPIDs += $proc.Id
Start-Sleep -Seconds 8

try {
    $null = Invoke-WebRequest -Uri "http://localhost:3000" -UseBasicParsing -ErrorAction SilentlyContinue
    Write-Host "       Dashboard ready" -ForegroundColor Green
} catch {
    Write-Host "       Dashboard may still be compiling..." -ForegroundColor DarkYellow
}

# ─── Ready ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ===========================================" -ForegroundColor DarkGray
Write-Host "  All services running!" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:      http://localhost:3000" -ForegroundColor Cyan
Write-Host "  Backend:        http://localhost:8080" -ForegroundColor Cyan
Write-Host "  Intelligence:   http://localhost:8081" -ForegroundColor Cyan
Write-Host "  API Status:     http://localhost:8080/api/status" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Robots: PiCar-X + TurtleBot + 3x Locus AMR (mock)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Ctrl+C to stop everything." -ForegroundColor DarkGray
Write-Host ""

# ─── Wait and cleanup on Ctrl+C ─────────────────────────────────────────────
try {
    while ($true) { Start-Sleep -Seconds 1 }
} finally {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor Yellow

    # Kill tracked child cmd.exe processes (and their children)
    foreach ($pid in $childPIDs) {
        try {
            # taskkill /T kills the process tree (cmd + go/python/node)
            taskkill /F /T /PID $pid 2>$null | Out-Null
        } catch {}
    }

    # Safety net: kill anything still on the ports
    foreach ($port in @(8080, 8081, 3000)) {
        $lines = netstat -aon 2>$null | Select-String ":$port\s.*LISTEN"
        foreach ($line in $lines) {
            $pid = ($line -split '\s+')[-1]
            if ($pid -and $pid -ne "0") {
                try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
            }
        }
    }

    docker compose down 2>$null
    Write-Host "Done." -ForegroundColor Green
}
