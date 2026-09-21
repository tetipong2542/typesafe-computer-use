#!/usr/bin/env bash
# ==============================================================================
# e2e-tart-native-webmcp.sh: Live Native WebMCP Verification inside Tart macOS VM
# ==============================================================================

set -euo pipefail

VM_NAME="${1:-macos-worker}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "================================================================================"
echo " Starting Full Native WebMCP Live Verification on Tart VM: ${VM_NAME}"
echo "================================================================================"

VM_IP=$(tart ip "${VM_NAME}" 2>/dev/null || true)
if [[ -z "${VM_IP}" ]]; then
    echo "ERROR: VM '${VM_NAME}' is not running or has no IP address." >&2
    exit 1
fi
echo "[1/4] VM '${VM_NAME}' is active at IP: ${VM_IP}"

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
SSH_CMD="ssh ${SSH_OPTS} admin@${VM_IP}"

# 1. Sync updated files to Guest
echo "[2/4] Syncing latest codebase, fixtures, and scripts to Guest VM..."
rsync -avz --exclude '.venv' --exclude 'worker.db*' --exclude '.env' \
    "${PROJECT_ROOT}/typesafe_computer_use/" "admin@${VM_IP}:/Users/admin/typesafe-computer-use/typesafe_computer_use/" >/dev/null
rsync -avz --exclude '__pycache__' \
    "${PROJECT_ROOT}/scripts/" "admin@${VM_IP}:/Users/admin/typesafe-computer-use/scripts/" >/dev/null
rsync -avz --exclude '__pycache__' \
    "${PROJECT_ROOT}/tests/" "admin@${VM_IP}:/Users/admin/typesafe-computer-use/tests/" >/dev/null

# 2. Run Native WebMCP Live Test on Guest
echo "[3/4] Running Native WebMCP live test against Headful Chrome in Guest VM..."
${SSH_CMD} "
    cd /Users/admin/typesafe-computer-use
    set -a
    source .env
    set +a
    .venv/bin/python scripts/run_native_webmcp_e2e.py
"

# 3. Pull evidence back to host
echo "[4/4] Fetching evidence artifacts back to Host..."
mkdir -p "${PROJECT_ROOT}/docs/reports/phase-2d-native-webmcp-evidence"
scp ${SSH_OPTS} -r "admin@${VM_IP}:/Users/admin/typesafe-computer-use/docs/reports/phase-2d-native-webmcp-evidence/*" \
    "${PROJECT_ROOT}/docs/reports/phase-2d-native-webmcp-evidence/"

echo "================================================================================"
echo " Phase 2D-B Native WebMCP Verification Successfully Completed!"
echo "================================================================================"
