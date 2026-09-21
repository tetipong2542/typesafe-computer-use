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
if ! tart list | awk '{print $2}' | grep -q "^${VM_NAME}\$"; then
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
for i in {1..60}; do
    if VM_IP=$(tart ip "${VM_NAME}" 2>/dev/null) && [[ -n "${VM_IP}" ]]; then
        echo "VM acquired IP: ${VM_IP}"
        break
    fi
    echo "Waiting for IP... ($i/60)"
    sleep 3
done

if [[ -z "${VM_IP}" ]]; then
    echo "ERROR: Timed out waiting for VM IP address." >&2
    exit 1
fi

# 4. Provision SSH Key and Test Connectivity
echo "[4/5] Testing guest system connectivity and metadata..."
# Provision host public key via tart exec
HOST_PUB_KEY=""
if [[ -f "${HOME}/.ssh/id_ed25519.pub" ]]; then
    HOST_PUB_KEY=$(cat "${HOME}/.ssh/id_ed25519.pub")
elif [[ -f "${HOME}/.ssh/id_rsa.pub" ]]; then
    HOST_PUB_KEY=$(cat "${HOME}/.ssh/id_rsa.pub")
fi

if [[ -n "${HOST_PUB_KEY}" ]]; then
    tart exec "${VM_NAME}" /bin/bash -c "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '${HOST_PUB_KEY}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" 2>/dev/null || true
fi

SSH_KEY="${HOME}/.ssh/id_ed25519_tart"
SSH_KNOWN_HOSTS="${HOME}/.ssh/known_hosts_tart"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${SSH_KNOWN_HOSTS} -o BatchMode=yes -o ConnectTimeout=5"
if [[ -f "${SSH_KEY}" ]]; then
    SSH_OPTS="-i ${SSH_KEY} ${SSH_OPTS}"
fi

ssh-keyscan -H "${VM_IP}" >> "${SSH_KNOWN_HOSTS}" 2>/dev/null || true

if ssh ${SSH_OPTS} admin@"${VM_IP}" "true" 2>/dev/null; then
    echo "SSH connection established successfully."
    ssh ${SSH_OPTS} admin@"${VM_IP}" "
        echo '=== Remote macOS System Info via SSH ==='
        sw_vers
        uname -m
        echo 'Disk Free:'
        df -h /
    "
else
    echo "SSH key authentication not active; querying system metadata directly via tart exec..."
    echo '=== Remote macOS System Info via tart exec ==='
    tart exec "${VM_NAME}" sw_vers
    tart exec "${VM_NAME}" uname -m
    tart exec "${VM_NAME}" df -h /
fi

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
