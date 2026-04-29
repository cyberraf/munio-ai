#!/usr/bin/env bash
#
# AI Security Brain — End-to-end pipeline test
# Starts infra, backend, mock agent, then verifies every API endpoint.
#

set -uo pipefail

# ─── Paths & config ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CORE_DIR="$PROJECT_DIR/services/core"
AGENT_DIR="$PROJECT_DIR/agent"
API="http://localhost:8080/api"
CH_HTTP="http://localhost:8123"
# Use a temp file both bash and Python can access.
# cygpath converts /tmp/... to C:\Users\...\AppData\Local\Temp\... for Python on Windows.
TEST_OUT="/tmp/asb_test_output.json"
if command -v cygpath &>/dev/null; then
  TEST_OUT_PY=$(cygpath -w "$TEST_OUT")
else
  TEST_OUT_PY="$TEST_OUT"
fi

export PATH="/c/Program Files/Go/bin:$PATH"

# ─── State ───────────────────────────────────────────────────────────────────
PIDS=()
AGENT_PID=""
PASSED=0
FAILED=0
FAILURES=""

# ─── Helpers ─────────────────────────────────────────────────────────────────
info()  { echo -e "\n\033[1;34m► $*\033[0m"; }
ok()    { echo "  ✓ $*"; PASSED=$((PASSED + 1)); }
fail()  { echo "  ✗ $*"; FAILED=$((FAILED + 1)); FAILURES="${FAILURES}\n  - $*"; }

# check <label> <python_expression>
# Fetches $TEST_OUT file content as JSON, runs the expression.
# Expression must print OK|<msg> or FAIL|<msg>.
check() {
  local label="$1" expr="$2"
  RESULT=$(python3 -c "
import json, sys
try:
    with open(r'$TEST_OUT_PY', 'r') as f:
        d = json.load(f)
except Exception as e:
    print(f'FAIL|could not parse JSON: {e}')
    sys.exit(0)
$expr
" 2>/dev/null)
  if [[ "$RESULT" == OK* ]]; then
    ok "$label: ${RESULT#OK|}"
  else
    fail "$label: ${RESULT#FAIL|}"
  fi
}

cleanup() {
  info "Cleaning up"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null && echo "  killed PID $pid" || true
  done
  if command -v netstat &>/dev/null; then
    netstat -aon 2>/dev/null | grep ":8080.*LISTEN" | awk '{print $5}' | while read -r p; do
      taskkill //F //PID "$p" 2>/dev/null || true
    done
  fi
  cd "$PROJECT_DIR"
  docker compose down 2>/dev/null || true
  rm -f "$CORE_DIR/server_test_bin" "$CORE_DIR/server_test_bin.exe"
  rm -f "$PROJECT_DIR/scripts/.core.log" "$PROJECT_DIR/scripts/.agent.log"
  rm -f "$TEST_OUT"
  echo "  done"
}
trap cleanup EXIT

# ─── 1. Start infrastructure ────────────────────────────────────────────────
set -e

info "Starting Docker Compose (ClickHouse + PostgreSQL)"
cd "$PROJECT_DIR"
docker compose down -v 2>/dev/null || true
docker compose up -d 2>&1 | grep -E "Started|Created|Error" || true

info "Waiting for databases"

for i in $(seq 1 30); do
  result=$(curl -sf "$CH_HTTP/?user=default&password=asb_dev" -d "SELECT 1" 2>/dev/null) || true
  if [[ "$result" == "1" ]]; then echo "  ClickHouse ready (attempt $i)"; break; fi
  if [[ $i -eq 30 ]]; then echo "  ClickHouse NOT ready after 30s"; exit 1; fi
  sleep 1
done

for i in $(seq 1 20); do
  if docker compose exec -T postgres pg_isready -U asb -q 2>/dev/null; then
    echo "  PostgreSQL ready (attempt $i)"; break
  fi
  if [[ $i -eq 20 ]]; then echo "  PostgreSQL NOT ready after 20s"; exit 1; fi
  sleep 1
done

# ─── 2. Build & start asb-core ──────────────────────────────────────────────
info "Building asb-core"
cd "$CORE_DIR"
go build -o "$CORE_DIR/server_test_bin" ./cmd/server 2>&1
echo "  build ok"

info "Starting asb-core"
"$CORE_DIR/server_test_bin" > "$PROJECT_DIR/scripts/.core.log" 2>&1 &
PIDS+=($!)
echo "  PID ${PIDS[-1]}"

for i in $(seq 1 15); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$API/status" 2>/dev/null) || true
  if [[ "$code" =~ ^2 ]]; then echo "  asb-core ready (attempt $i)"; break; fi
  if [[ $i -eq 15 ]]; then echo "  asb-core NOT ready after 15s"; exit 1; fi
  sleep 1
done

# ─── 3. Start mock telemetry agent ──────────────────────────────────────────
info "Starting mock telemetry agent"
cd "$AGENT_DIR"
python3 -u picar_telemetry_agent.py --mock --url ws://localhost:8080/ws/telemetry \
  > "$PROJECT_DIR/scripts/.agent.log" 2>&1 &
AGENT_PID=$!
PIDS+=($AGENT_PID)
echo "  PID $AGENT_PID"

# ─── 4. Let data flow ───────────────────────────────────────────────────────
info "Streaming telemetry for 10 seconds..."
sleep 10

# ─── 5. Verification checks ─────────────────────────────────────────────────
set +e

info "Running verification checks"

# ── 5a. GET /api/status ──────────────────────────────────────────────────────
curl -sf "$API/status" > "$TEST_OUT" 2>/dev/null
check "status" "
c = d.get('robot_connected', False)
t = d.get('total_events', 0)
db = d.get('db_status', '')
errs = []
if not c: errs.append(f'robot_connected={c}')
if t <= 50: errs.append(f'total_events={t} (expected >50)')
if db != 'ok': errs.append(f'db_status={db}')
if errs: print('FAIL|' + '; '.join(errs))
else: print(f'OK|robot_connected=true total_events={t} db_status=ok')
"

# ── 5b. GET /api/telemetry/latest ────────────────────────────────────────────
LATEST_CODE=$(curl -s -o "$TEST_OUT" -w "%{http_code}" "$API/telemetry/latest" 2>/dev/null)
if [[ "$LATEST_CODE" == "200" ]]; then
  check "telemetry/latest" "
rid = d.get('robot_id', '')
if rid: print(f'OK|robot_id={rid}')
else: print('FAIL|missing robot_id')
"
else
  fail "telemetry/latest: HTTP $LATEST_CODE (expected 200)"
fi

# ── 5c. GET /api/incidents?limit=5 ──────────────────────────────────────────
curl -sf "$API/incidents?limit=5" > "$TEST_OUT" 2>/dev/null
check "incidents" "
n = len(d)
if n >= 1: print(f'OK|{n} found (first: {d[0][\"event_type\"]})')
else: print('FAIL|0 incidents (expected >=1)')
"

# ── 5d. GET /api/metrics?range=1h ───────────────────────────────────────────
curl -sf "$API/metrics?range=1h" > "$TEST_OUT" 2>/dev/null
check "metrics" "
te = d.get('total_events', 0)
ti = d.get('total_incidents', 0)
if te > 0: print(f'OK|total_events={te} total_incidents={ti}')
else: print(f'FAIL|total_events={te} (expected >0)')
"

# ── 5e. GET /api/config/thresholds ───────────────────────────────────────────
curl -sf "$API/config/thresholds" > "$TEST_OUT" 2>/dev/null
check "config/thresholds" "
p = d.get('proximity_cm')
s = d.get('speed_max')
if p and s: print(f'OK|proximity_cm={p} speed_max={s}')
else: print('FAIL|invalid response')
"

# ── 5f. POST /api/config/thresholds ─────────────────────────────────────────
curl -sf -X POST "$API/config/thresholds" \
  -H "Content-Type: application/json" \
  -d '{"proximity_cm":20,"speed_max":45,"off_path_grayscale":1400,"low_battery_v":5.5}' \
  > "$TEST_OUT" 2>/dev/null
check "config update" "
p = d.get('proximity_cm')
if p == 20 or p == 20.0: print(f'OK|proximity_cm={p}')
else: print(f'FAIL|proximity_cm={p} (expected 20)')
"

# Restore original thresholds
curl -sf -X POST "$API/config/thresholds" \
  -H "Content-Type: application/json" \
  -d '{"proximity_cm":30,"speed_max":60,"off_path_grayscale":1500,"low_battery_v":6.0}' \
  > /dev/null 2>&1 || true

# ── 5g. Stop agent, then reset ──────────────────────────────────────────────
# Kill agent first so no new incidents arrive after reset.
kill "$AGENT_PID" 2>/dev/null || true
sleep 1

curl -sf -X POST "$API/demo/reset" > "$TEST_OUT" 2>/dev/null
check "demo/reset" "
if d.get('status') == 'ok': print('OK|status=ok')
else: print(f'FAIL|status={d.get(\"status\")}')
"

# ── 5h. GET /api/incidents (should be empty after reset) ────────────────────
sleep 1
curl -sf "$API/incidents?limit=10" > "$TEST_OUT" 2>/dev/null
check "post-reset incidents" "
n = len(d)
if n == 0: print('OK|empty')
else: print(f'FAIL|{n} incidents remain (expected 0)')
"

# ── 5i. Verify ClickHouse was also truncated ─────────────────────────────────
CH_COUNT=$(curl -sf "$CH_HTTP/?user=default&password=asb_dev" -d "SELECT count() FROM telemetry" 2>/dev/null || echo "0")
CH_COUNT=$(echo "$CH_COUNT" | tr -d '[:space:]')
if [[ "$CH_COUNT" -lt 5 ]] 2>/dev/null; then
  ok "post-reset: clickhouse count=$CH_COUNT"
else
  fail "post-reset: clickhouse count=$CH_COUNT (expected <5 after reset, agent stopped)"
fi

# ─── 6. Results ──────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ $FAILED -eq 0 ]]; then
  echo -e "\033[1;32m  ALL $PASSED TESTS PASSED\033[0m"
else
  echo -e "\033[1;31m  $PASSED passed, $FAILED failed\033[0m"
  echo -e "\033[1;31m  Failures:$FAILURES\033[0m"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ $FAILED -gt 0 ]]; then
  echo "--- asb-core log (last 15 lines) ---"
  tail -15 "$PROJECT_DIR/scripts/.core.log" 2>/dev/null || echo "(no log)"
  echo ""
  echo "--- agent log (last 15 lines) ---"
  tail -15 "$PROJECT_DIR/scripts/.agent.log" 2>/dev/null || echo "(no log)"
fi

exit $FAILED
