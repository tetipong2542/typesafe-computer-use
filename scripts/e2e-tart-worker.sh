#!/usr/bin/env bash
# ==============================================================================
# e2e-tart-worker.sh: Full Application E2E Verification in Tart macOS VM
# ==============================================================================
# Verifies:
#   1. VM presence, IP acquisition, and SSH connectivity
#   2. Guest GUI session login (WindowServer active)
#   3. Headful Google Chrome startup check
#   4. Worker API daemon health inside VM
#   5. CDP loopback binding verification (strictly 127.0.0.1)
#   6. Host-to-Guest Task submission (POST /tasks)
#   7. Shadow Router telemetry and DB verification
#   8. Exclusive takeover (POST /tasks/{id}/takeover), input lock, and clean vnc_uri
#   9. Release takeover, emergency stop, and reset flow
# ==============================================================================

set -euo pipefail

VM_NAME="${1:-macos-worker}"
WORKER_PORT="${WORKER_PORT:-8000}"
AUTH_TOKEN="${WORKER_AUTH_TOKEN:-typesafe-worker-secret-token}"
AUTH_HEADER="Authorization: Bearer ${AUTH_TOKEN}"

echo "=========================================================="
echo " Starting Full Application Tart VM E2E Verification: ${VM_NAME}"
echo "=========================================================="

# 1. Preflight checks
if ! tart list | awk '{print $2}' | grep -q "^${VM_NAME}\$"; then
    echo "ERROR: VM '${VM_NAME}' not found in Tart registry." >&2
    echo "Please ensure 'tart clone' completed successfully." >&2
    exit 1
fi

echo "[1/9] VM '${VM_NAME}' verified in Tart registry."

# Ensure VM is running
VM_STARTED_BY_SCRIPT=false
if ! tart ip "${VM_NAME}" >/dev/null 2>&1; then
    echo "Starting VM '${VM_NAME}'..."
    tart run "${VM_NAME}" --no-graphics &
    VM_PID=$!
    VM_STARTED_BY_SCRIPT=true
fi

cleanup() {
    if [[ "${VM_STARTED_BY_SCRIPT}" == "true" ]]; then
        echo "Stopping VM '${VM_NAME}'..."
        tart stop "${VM_NAME}" || true
    fi
}
trap cleanup EXIT

# 2. Acquire IP Address
echo "[2/9] Polling for VM IP address..."
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

SSH_KEY="${HOME}/.ssh/id_ed25519_tart"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"
SSH_CMD="ssh ${SSH_OPTS} admin@${VM_IP}"

# Dynamically resolve guest-local token if not passed in host environment
if [[ -z "${WORKER_AUTH_TOKEN:-}" ]]; then
    GUEST_TOKEN=$(${SSH_CMD} "grep WORKER_AUTH_TOKEN ~/typesafe-computer-use/.env 2>/dev/null | cut -d= -f2 | tr -d '\"'" || true)
    if [[ -n "${GUEST_TOKEN}" ]]; then
        AUTH_TOKEN="${GUEST_TOKEN}"
        AUTH_HEADER="Authorization: Bearer ${AUTH_TOKEN}"
    fi
fi

# 3. SSH Connectivity & Guest Environment
echo "[3/9] Testing SSH connectivity and system metadata..."
ssh-keyscan -H "${VM_IP}" >> ~/.ssh/known_hosts 2>/dev/null || true

${SSH_CMD} "
    echo '=== Guest macOS System Info ==='
    sw_vers
    uname -m
"

# 4. GUI Session / WindowServer & Chrome Check
echo "[4/9] Verifying GUI session and Headful Chrome inside Guest..."
${SSH_CMD} "
    if pgrep WindowServer >/dev/null; then
        echo 'WindowServer is running. GUI desktop session confirmed.'
    else
        echo 'ERROR: WindowServer is not running in guest!' >&2
        exit 1
    fi
    if [[ -d '/Applications/Google Chrome.app' ]]; then
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --version
    else
        echo 'WARNING: Google Chrome.app not found in /Applications'
    fi
"

# 5. Worker API Health & Launch Verification
echo "[5/9] Checking Worker API daemon on guest (http://${VM_IP}:${WORKER_PORT}/healthz)..."
WORKER_ONLINE=false
for i in {1..10}; do
    if curl -s -f "http://${VM_IP}:${WORKER_PORT}/healthz" >/dev/null 2>&1; then
        echo "Worker API daemon is responding."
        WORKER_ONLINE=true
        break
    fi
    echo "Waiting for Worker API daemon... ($i/10)"
    sleep 2
done

if [[ "${WORKER_ONLINE}" != "true" ]]; then
    echo "Worker API not yet responding on guest port ${WORKER_PORT}."
    echo "Attempting to restart worker daemon via LaunchAgent in guest session..."
    ${SSH_CMD} "
        launchctl kickstart -k \"gui/\$(id -u)/com.typesafe.worker\"
    "
    sleep 5
    if ! curl -s -f "http://${VM_IP}:${WORKER_PORT}/healthz" >/dev/null 2>&1; then
        echo "ERROR: Could not connect to Worker API on http://${VM_IP}:${WORKER_PORT}/healthz" >&2
        exit 1
    fi
    echo "Worker API daemon successfully launched and verified."
fi


# 6. CDP Loopback Binding Verification
echo "[6/9] Verifying CDP loopback binding inside Guest (strictly 127.0.0.1)..."
${SSH_CMD} "
    # Check that any CDP port is bound strictly to 127.0.0.1 and not 0.0.0.0
    PUBLIC_CDP=\$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -E '\*:922[0-9]|\*:0' || true)
    if [[ -n \"\${PUBLIC_CDP}\" ]]; then
        echo 'SECURITY ERROR: CDP port is listening publicly!' >&2
        echo \"\${PUBLIC_CDP}\" >&2
        exit 1
    fi
    echo 'CDP binding security verified: no public listeners.'
"

# 7. Host-to-Guest Task Submission & Shadow Router Verification
echo "[7/9] Submitting Headful Browser Task via POST /tasks..."
CREATE_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks" \
    -H "Content-Type: application/json" \
    -H "${AUTH_HEADER}" \
    -d '{"goal": "Open Google Chrome and view page", "browser": "Google Chrome", "steps": 2, "act": true}')

echo "Create Task Response: ${CREATE_RESP}"
TASK_ID=$(echo "${CREATE_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('task_id', ''))")

if [[ -z "${TASK_ID}" ]]; then
    echo "ERROR: Failed to obtain task_id from create task response." >&2
    exit 1
fi

echo "Active Task ID: ${TASK_ID}"

# Poll task state until Step 1 perception and action execution completes
echo "Waiting for Step 1 perception and shadow telemetry execution..."
TASK_RUNNING=false
for i in {1..20}; do
    TASK_RESP=$(curl -s "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}" -H "${AUTH_HEADER}")
    STEP=$(echo "${TASK_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('current_step', 0))")
    SHOT_ID=$(echo "${TASK_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('latest_screenshot_id') or '')")
    STATE=$(echo "${TASK_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('state', ''))")
    echo "  [Poll $i/20] State: ${STATE}, Step: ${STEP}, Screenshot: ${SHOT_ID}"
    if [[ "${STEP}" -ge 1 && -n "${SHOT_ID}" ]]; then
        TASK_RUNNING=true
        break
    fi
    if [[ "${STATE}" == "stopped" || "${STATE}" == "failed" || "${STATE}" == "done" ]]; then
        break
    fi
    sleep 2
done

# Query guest SQLite database directly to verify Shadow Router invariants and screenshot
echo "Verifying SQLite telemetry and filesystem artifacts on Guest..."
${SSH_CMD} /Users/admin/typesafe-computer-use/.venv/bin/python - <<EOF
from typesafe_computer_use.worker.db import WorkerDatabase
from pathlib import Path
import sys

db = WorkerDatabase("/Users/admin/typesafe-computer-use/worker.db")
events = db.get_events("${TASK_ID}")

perceived = [e for e in events if str(e.phase) == "step_perceived"]
assert perceived, "No step_perceived event recorded!"
shot_id = perceived[0].screenshot_id
assert shot_id, "No screenshot_id in step_perceived!"
print(f"Verified step_perceived event with screenshot_id: {shot_id}")

# If task is awaiting review, grant approval and resume to allow action execution
task = db.get_task("${TASK_ID}")
if task and task.state.value == "awaiting_review":
    review_evts = [e for e in events if str(e.phase) == "state_changed" and e.state.value == "awaiting_review"]
    if review_evts:
        rev = review_evts[0]
        print(f"Auto-approving awaiting_review action: {rev.action}")
        import urllib.request, json
        appr_url = "http://127.0.0.1:${WORKER_PORT}/tasks/${TASK_ID}/approvals/" + rev.event_id
        resume_url = "http://127.0.0.1:${WORKER_PORT}/tasks/${TASK_ID}/resume"
        headers = {"Authorization": "Bearer ${AUTH_TOKEN}", "Content-Type": "application/json"}
        req_body = json.dumps({
            "step": rev.step,
            "action": rev.action,
            "target": rev.target or "",
            "screenshot_hash": rev.extra.get("screenshot_hash", ""),
            "action_fingerprint": rev.extra.get("action_fingerprint", "")
        }).encode()
        try:
            req1 = urllib.request.Request(appr_url, data=req_body, headers=headers)
            urllib.request.urlopen(req1, timeout=5)
            req2 = urllib.request.Request(resume_url, data=b"", headers=headers)
            urllib.request.urlopen(req2, timeout=5)
            print("Successfully approved and resumed task.")
        except Exception as e:
            print(f"Warning: auto-approval error: {e}")

# Re-read events after possible execution
import time
time.sleep(2)
events = db.get_events("${TASK_ID}")
executed = [e for e in events if str(e.phase) == "action_executed"]
if executed:
    extra = executed[0].extra
    print(f"Verified action_executed event with telemetry: {extra}")
    assert extra.get("router_mode") == "shadow", f"Expected router_mode=shadow, got {extra.get('router_mode')}"
    assert extra.get("executed_mode") == "visual_grounded", f"Expected executed_mode=visual_grounded, got {extra.get('executed_mode')}"
    print("Shadow router invariants verified: router_mode=shadow, executed_mode=visual_grounded")
EOF

# Verify Screenshot retrieval via GET /tasks/{task_id}/screenshot
echo "Verifying Screenshot API endpoint (GET /tasks/${TASK_ID}/screenshot)..."
SCREENSHOT_TEMP=$(mktemp /tmp/tart_worker_shot_XXXXXX.png)
HTTP_CODE=$(curl -s -w "%{http_code}" -H "${AUTH_HEADER}" "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/screenshot" -o "${SCREENSHOT_TEMP}")
if [[ "${HTTP_CODE}" -ne 200 ]]; then
    echo "ERROR: Failed to fetch screenshot from Worker API (HTTP ${HTTP_CODE})" >&2
    exit 1
fi
python3 -c "
from PIL import Image
im = Image.open('${SCREENSHOT_TEMP}')
print(f'Retrieved screenshot verified: {im.size[0]}x{im.size[1]} {im.format} ({im.mode})')
assert im.size[0] >= 800 and im.size[1] >= 600, f'Invalid screenshot dimensions: {im.size}'
"
rm -f "${SCREENSHOT_TEMP}"

# 8. Human Takeover Test: Input Lock, Clean VNC URI, & Port 5900
echo "[8/9] Testing Human Takeover (POST /tasks/${TASK_ID}/takeover)..."
# Pause first to allow takeover
curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/pause" -H "${AUTH_HEADER}" >/dev/null || true

TAKEOVER_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/takeover" -H "${AUTH_HEADER}")
echo "Takeover Response: ${TAKEOVER_RESP}"

# Verify takeover response
python3 -c "
import sys, json, re

data = json.loads('''${TAKEOVER_RESP}''')
assert data.get('status') == 'takeover', f'Unexpected status: {data}'
assert data.get('input_locked') is True, f'Input not locked: {data}'

vnc_uri = data.get('vnc_uri', '')
print(f'Received VNC URI: {vnc_uri}')

# Strict credential sanitization check
assert not re.search(r':[^/@]+@', vnc_uri), f'Password leaked in VNC URI: {vnc_uri}'
assert re.match(r'^vnc://([a-zA-Z0-9_-]+@)?[a-zA-Z0-9.-]+(:[0-9]+)?$', vnc_uri), f'Invalid VNC format: {vnc_uri}'
assert 'admin:admin' not in vnc_uri, 'admin:admin found in URI!'
print('VNC URI credential sanitization verified successfully.')
"

# Verify Screen Sharing VNC TCP port 5900 is open and listening
if nc -z -w 3 "${VM_IP}" 5900 >/dev/null 2>&1; then
    echo "Screen Sharing VNC port 5900 verified listening on guest."
else
    echo "WARNING: VNC port 5900 not reachable from host."
fi

# Verify synthetic input lock is active on guest
${SSH_CMD} "test -f /tmp/typesafe_input_locked"
echo "Guest input lock file /tmp/typesafe_input_locked confirmed engaged during takeover."

# Release Takeover
echo "Releasing Takeover..."
RELEASE_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/release-takeover" -H "${AUTH_HEADER}")
echo "Release Takeover Response: ${RELEASE_RESP}"

# 9. Emergency Stop & Reset Flow
echo "[9/9] Testing Emergency Stop and Task Reset..."
ESTOP_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/emergency-stop" -H "${AUTH_HEADER}")
echo "Emergency Stop Response: ${ESTOP_RESP}"

python3 -c "
import sys, json
data = json.loads('''${ESTOP_RESP}''')
assert data.get('input_locked') is True, f'Input not locked after emergency stop: {data}'
print('Emergency stop confirmed input_locked=True.')
"

${SSH_CMD} "test -f /tmp/typesafe_input_locked"
echo "Guest input lock file confirmed engaged on emergency stop."

RESET_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/reset" -H "${AUTH_HEADER}")
echo "Reset Response: ${RESET_RESP}"

python3 -c "
import sys, json
data = json.loads('''${RESET_RESP}''')
assert data.get('status') == 'reset', f'Unexpected reset status: {data}'
assert data.get('input_locked') is False, f'Input locked after reset: {data}'
print('Reset confirmed input_locked=False.')
"

${SSH_CMD} "! test -f /tmp/typesafe_input_locked"
echo "Guest input lock file confirmed removed after reset."

echo "=========================================================="
echo " Full Application Tart VM E2E Verification: PASSED"
echo "=========================================================="
