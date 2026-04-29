# AI Security Brain — Stop all services
Write-Host "Stopping all services..." -ForegroundColor Yellow

# Kill processes on known ports
$ports = @(8080, 3000)
foreach ($port in $ports) {
    $connections = netstat -aon 2>$null | Select-String ":$port\s.*LISTEN"
    foreach ($conn in $connections) {
        $procId = ($conn -split '\s+')[-1]
        if ($procId -and $procId -ne "0") {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host "  Killed PID $procId (port $port)" -ForegroundColor DarkGray
        }
    }
}

# Stop Docker Compose
docker compose down 2>$null
Write-Host "Done." -ForegroundColor Green
