#!/usr/bin/env bash
# ==============================================================================
# e2e-tart-api-hybrid.sh: Full API-Driven Hybrid Verification in Tart VM
# ==============================================================================
# Runs a 100% API-driven E2E verification test against the live Worker daemon:
#   1. Submits Task via POST /tasks
#   2. Verifies execution strictly through Worker Supervisor -> Planner -> Router
#   3. Confirms DOM mutation via BrowserDOMAdapter (Step 1)
#   4. Confirms automatic fallback to VisualComputerUseAdapter (Step 2)
#   5. Verifies zero direct DB manipulation from test script
#   6. Collects full evidence (SSE, SQLite dump, screenshot)
# ==============================================================================

set -euo pipefail

VM_NAME="${1:-macos-worker}"
WORKER_PORT="${WORKER_PORT:-8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "================================================================================"
echo " Starting Full API-Driven Tart E2E Hybrid Verification: ${VM_NAME}"
echo "================================================================================"

# 1. Check Tart VM
if ! tart list | awk '{print $2}' | grep -q "^${VM_NAME}\$"; then
    echo "ERROR: VM '${VM_NAME}' not found in Tart registry." >&2
    exit 1
fi

VM_IP=$(tart ip "${VM_NAME}" 2>/dev/null || true)
if [[ -z "${VM_IP}" ]]; then
    echo "ERROR: VM '${VM_NAME}' is not running or has no IP address." >&2
    exit 1
fi
echo "[1/5] VM '${VM_NAME}' is active at IP: ${VM_IP}"

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
SSH_CMD="ssh ${SSH_OPTS} admin@${VM_IP}"

# 2. Sync codebase and scripts to Guest VM
echo "[2/5] Syncing latest code and test scripts to Guest VM..."
rsync -avz --exclude '.venv' --exclude 'worker.db*' --exclude '.env' \
    "${PROJECT_ROOT}/typesafe_computer_use/" "admin@${VM_IP}:/Users/admin/typesafe-computer-use/typesafe_computer_use/" >/dev/null
rsync -avz --exclude '__pycache__' \
    "${PROJECT_ROOT}/scripts/" "admin@${VM_IP}:/Users/admin/typesafe-computer-use/scripts/" >/dev/null
rsync -avz --exclude '__pycache__' \
    "${PROJECT_ROOT}/tests/" "admin@${VM_IP}:/Users/admin/typesafe-computer-use/tests/" >/dev/null

# 3. Ensure clean Chrome state and fixture
echo "[3/5] Preparing Chrome fixture in Guest VM..."
${SSH_CMD} "
    # Dismiss any open menu
    osascript -e 'tell application \"System Events\" to key code 53' >/dev/null 2>&1 || true
    # Reload fixture page
    osascript -e 'tell application \"Google Chrome\" to reload active tab of front window' >/dev/null 2>&1 || true
"

# 4. Run pure API-driven E2E runner in Guest VM
echo "[4/5] Running pure API-driven E2E verification..."
${SSH_CMD} "
    cd /Users/admin/typesafe-computer-use
    set -a
    source .env
    set +a
    .venv/bin/python scripts/run_api_hybrid_closure_e2e.py \
        --worker-url http://127.0.0.1:${WORKER_PORT} \
        --auth-token \"\${WORKER_AUTH_TOKEN}\" \
        --db-path /Users/admin/typesafe-computer-use/worker.db \
        --evidence-dir /Users/admin/typesafe-computer-use/docs/reports/phase-2c-e2e-evidence
"

# 5. Fetch evidence artifacts back to Host
echo "[5/5] Fetching evidence artifacts back to Host..."
mkdir -p "${PROJECT_ROOT}/docs/reports/phase-2c-e2e-evidence"
scp ${SSH_OPTS} -r "admin@${VM_IP}:/Users/admin/typesafe-computer-use/docs/reports/phase-2c-e2e-evidence/*" \
    "${PROJECT_ROOT}/docs/reports/phase-2c-e2e-evidence/"

echo "================================================================================"
echo " Phase 2C API-Driven Verification Successfully Completed!"
echo " Evidence synced to: ${PROJECT_ROOT}/docs/reports/phase-2c-e2e-evidence"
echo "================================================================================"
