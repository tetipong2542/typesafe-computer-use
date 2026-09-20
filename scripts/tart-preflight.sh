#!/usr/bin/env bash
# ==============================================================================
# tart-preflight.sh: Preflight verification for Tart macOS VM Worker
# ==============================================================================

set -euo pipefail

VM_NAME="${1:-macos-worker}"

echo "=========================================================="
echo " Starting Preflight Verification for Tart VM: ${VM_NAME}"
echo "=========================================================="

# 1. Check if VM exists
if ! tart list | grep -q "^${VM_NAME}\$"; then
    echo "ERROR: VM '${VM_NAME}' not found. Please wait for 'tart clone' to complete." >&2
    exit 1
fi

echo "[1/5] VM '${VM_NAME}' is present in Tart registry."

# 2. Check if VM is already running or launch in background
VM_STARTED_BY_SCRIPT=false
if ! tart ip "${VM_NAME}" >/dev/null 2>&1; then
    echo "[2/5] Starting VM in headless background mode..."
    tart run "${VM_NAME}" --no-graphics &
    VM_PID=$!
    VM_STARTED_BY_SCRIPT=true
else
    echo "[2/5] VM '${VM_NAME}' is already running."
fi

cleanup() {
    if [[ "${VM_STARTED_BY_SCRIPT}" == "true" ]]; then
        echo "Cleaning up VM process started by preflight..."
        tart stop "${VM_NAME}" || true
    fi
}
trap cleanup EXIT

# 3. Wait for IP Address
echo "[3/5] Waiting for VM to acquire IP address..."
VM_IP=""
for i in {1..30}; do
    if VM_IP=$(tart ip "${VM_NAME}" 2>/dev/null) && [[ -n "${VM_IP}" ]]; then
        echo "VM acquired IP: ${VM_IP}"
        break
    fi
    echo "Waiting for IP... ($i/30)"
    sleep 3
done

if [[ -z "${VM_IP}" ]]; then
    echo "ERROR: Timed out waiting for VM IP address." >&2
    exit 1
fi

# 4. Test SSH Connectivity
echo "[4/5] Testing SSH connectivity and system metadata inside VM..."
ssh-keyscan -H "${VM_IP}" >> ~/.ssh/known_hosts 2>/dev/null || true

ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 admin@"${VM_IP}" "
    echo '=== Remote macOS System Info ==='
    sw_vers
    uname -m
    echo 'Disk Free:'
    df -h /
"

# 5. Test VNC Port Accessibility
echo "[5/5] Testing VNC port 5900 availability for Human Takeover..."
if nc -z -w 5 "${VM_IP}" 5900 2>/dev/null; then
    echo "VNC port 5900 is listening and accessible."
    echo "Takeover URI verified: vnc://admin@${VM_IP}"
else
    echo "WARNING: VNC port 5900 is not yet reachable (Screen Sharing may need to be enabled in System Settings)."
fi

echo "=========================================================="
echo " Tart VM Preflight Verification: SUCCESS"
echo "=========================================================="
