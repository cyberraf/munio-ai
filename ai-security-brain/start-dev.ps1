# AI Security Brain - Start services (no mock agents)
# Usage: .\start-dev.ps1
#   Starts databases, backend, intelligence engine, and dashboard.
#   Robots are added via the dashboard Add Robot flow.
#   Press Ctrl+C to stop everything.

$ErrorActionPreference = "Stop"

# --- Resolve tool paths ---
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
if (-not (Get-Command go     -ErrorAction SilentlyContinue)) { $missing += "go" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { $missing += "docker" }
if (-not (Get-Command npm    -ErrorAction SilentlyContinue)) { $missing += "npm" }

# Find a Python interpreter that actually has the intelligence-engine deps.
# Several Pythons may be on PATH (Microsoft Store stub, system Python, etc.) —
# only the one with `uvicorn` installed is usable. Without this check the
# script silently picks the Store stub and uvicorn dies with "No module".
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
    $missing += "python with uvicorn (run: pip install -r services/intelligence/requirements.txt)"
}

if ($missing.Count -gt 0) {
    Write-Host "Missing required tools:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor Red }
    exit 1
}

# --- Kill leftover processes from a previous run ---
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
Write-Host "  AI Security Brain - Development Mode (no mocks)" -ForegroundColor Cyan
Write-Host "  ================================================" -ForegroundColor DarkGray
Write-Host ""

$childPIDs = @()

# --- 1. Start databases ---
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

# --- 2. Start Go backend ---
Write-Host ""
Write-Host "[2/4] Starting Go backend on :8080..." -ForegroundColor Yellow

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

# --- 3. Start Intelligence Engine ---
Write-Host ""
Write-Host "[3/4] Starting Intelligence Engine on :8081..." -ForegroundColor Yellow

$intelDir = Join-Path $PWD "services\intelligence"
$proc = Start-Process cmd -ArgumentList "/c","cd /d `"$intelDir`" && $pythonCmd -m uvicorn app.main:app --host 0.0.0.0 --port 8081" -PassThru -NoNewWindow
$childPIDs += $proc.Id
Start-Sleep -Seconds 4

try {
    # `/openapi.json` is always available on a running FastAPI instance —
    # `/health` collides with a parameterized router and returns 422.
    $resp = Invoke-WebRequest -Uri "http://localhost:8081/openapi.json" -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        Write-Host "       Intelligence Engine running" -ForegroundColor Green
    } else {
        Write-Host "       Intelligence Engine may still be starting..." -ForegroundColor DarkYellow
    }
} catch {
    Write-Host "       Intelligence Engine may still be starting..." -ForegroundColor DarkYellow
}

# --- 4. Start dashboard ---
Write-Host ""
Write-Host "[4/4] Starting dashboard on :3000..." -ForegroundColor Yellow

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

# --- Ready ---
Write-Host ""
Write-Host "  ================================================" -ForegroundColor DarkGray
Write-Host "  All services running!" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:      http://localhost:3000" -ForegroundColor Cyan
Write-Host "  Backend:        http://localhost:8080" -ForegroundColor Cyan
Write-Host "  Intelligence:   http://localhost:8081" -ForegroundColor Cyan
Write-Host "  API Status:     http://localhost:8080/api/status" -ForegroundColor Cyan
Write-Host ""
Write-Host "  No mock agents - add real robots via the dashboard." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Ctrl+C to stop everything." -ForegroundColor DarkGray
Write-Host ""

# --- Wait and cleanup on Ctrl+C ---
try {
    while ($true) { Start-Sleep -Seconds 1 }
} finally {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor Yellow

    foreach ($pid in $childPIDs) {
        try {
            taskkill /F /T /PID $pid 2>$null | Out-Null
        } catch {}
    }

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
