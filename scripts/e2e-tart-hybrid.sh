#!/usr/bin/env bash
# ==============================================================================
# e2e-tart-hybrid.sh: Full Structured Hybrid Execution (Phase 2C) Tart VM E2E
# ==============================================================================
# Verifies:
#   1. VM presence, IP acquisition, and SSH connectivity
#   2. Configuration of INTERACTION_ROUTER_MODE="hybrid" in guest .env
#   3. Clean worker daemon restart via LaunchAgent
#   4. Health and loopback CDP binding verification (strictly 127.0.0.1)
#   5. Submission of live task via POST /tasks under hybrid mode
#   6. Verification of hybrid telemetry (selected_mode, executed_mode, fallback_count) in SQLite
#   7. Live annotated screenshot retrieval via HTTP GET /tasks/{id}/screenshot
#   8. Takeover, emergency stop, and input-lock isolation validation under hybrid mode
# ==============================================================================

set -euo pipefail

VM_NAME="${1:-macos-worker}"
WORKER_PORT="${WORKER_PORT:-8000}"
SSH_KEY="${HOME}/.ssh/id_ed25519_tart"
SSH_KNOWN_HOSTS="${HOME}/.ssh/known_hosts_tart"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${SSH_KNOWN_HOSTS} -o ConnectTimeout=10"

echo "=========================================================="
echo " Phase 2C Structured Hybrid Execution E2E: ${VM_NAME}"
echo "=========================================================="

# 1. Preflight VM checks
if ! tart list | awk '{print $2}' | grep -q "^${VM_NAME}\$"; then
    echo "ERROR: VM '${VM_NAME}' not found in Tart registry." >&2
    exit 1
fi

echo "[1/8] Acquiring VM IP address..."
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

SSH_CMD="ssh ${SSH_OPTS} admin@${VM_IP}"

# 2. Configure INTERACTION_ROUTER_MODE=hybrid in guest .env
echo "[2/8] Setting INTERACTION_ROUTER_MODE=hybrid in guest .env..."
${SSH_CMD} "
    ENV_FILE=\"\$HOME/typesafe-computer-use/.env\"
    if grep -q '^INTERACTION_ROUTER_MODE=' \"\${ENV_FILE}\"; then
        sed -i '' 's/^INTERACTION_ROUTER_MODE=.*/INTERACTION_ROUTER_MODE=\"hybrid\"/' \"\${ENV_FILE}\"
    else
        echo 'INTERACTION_ROUTER_MODE=\"hybrid\"' >> \"\${ENV_FILE}\"
    fi
    grep '^INTERACTION_ROUTER_MODE=' \"\${ENV_FILE}\"
"

# 3. Clean restart of LaunchAgent daemon in guest session
echo "[3/8] Restarting Worker LaunchAgent daemon with hybrid mode..."
${SSH_CMD} "
    # Clear any leftover locks
    rm -f /tmp/typesafe_input_locked /tmp/typesafe_takeover_active
    launchctl kickstart -k \"gui/\$(id -u)/com.typesafe.worker\"
"
sleep 4

# Read token
AUTH_TOKEN=$(${SSH_CMD} "grep WORKER_AUTH_TOKEN ~/typesafe-computer-use/.env | cut -d= -f2 | tr -d '\"'")
AUTH_HEADER="Authorization: Bearer ${AUTH_TOKEN}"

# 4. Verify Health & CDP binding
echo "[4/8] Verifying Worker API health on http://${VM_IP}:${WORKER_PORT}/healthz..."
for i in {1..15}; do
    if curl -s -f "http://${VM_IP}:${WORKER_PORT}/healthz" >/dev/null 2>&1; then
        echo "Worker API health confirmed."
        break
    fi
    echo "Waiting for healthz... ($i/15)"
    sleep 2
done

# Verify CDP port loopback binding
${SSH_CMD} "
    PUBLIC_CDP=\$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -E '\*:922[0-9]|\*:0' || true)
    if [[ -n \"\${PUBLIC_CDP}\" ]]; then
        echo 'SECURITY ERROR: CDP port is listening publicly!' >&2
        exit 1
    fi
    echo 'CDP binding security verified: strictly loopback.'
"

# 5. Submit Hybrid Task
echo "[5/8] Submitting task under INTERACTION_ROUTER_MODE=hybrid..."
CREATE_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks" \
    -H "Content-Type: application/json" \
    -H "${AUTH_HEADER}" \
    -d '{"goal": "Check hybrid execution status in Google Chrome", "browser": "Google Chrome", "steps": 2, "act": true}')

echo "Create Task Response: ${CREATE_RESP}"
TASK_ID=$(echo "${CREATE_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('task_id', ''))")

if [[ -z "${TASK_ID}" ]]; then
    echo "ERROR: Failed to obtain task_id." >&2
    exit 1
fi

echo "Active Task ID: ${TASK_ID}"

# Poll task state
echo "[6/8] Polling for Step 1 & 2 perception and hybrid execution..."
for i in {1..40}; do
    TASK_RESP=$(curl -s "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}" -H "${AUTH_HEADER}")
    STEP=$(echo "${TASK_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('current_step', 0))")
    SHOT_ID=$(echo "${TASK_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('latest_screenshot_id') or '')")
    STATE=$(echo "${TASK_RESP}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('state', ''))")
    echo "  [Poll $i/40] State: ${STATE}, Step: ${STEP}, Screenshot: ${SHOT_ID}"

    # Auto-approve if in review state
    if [[ "${STATE}" == "awaiting_review" ]]; then
        echo "Auto-approving awaiting_review..."
        ${SSH_CMD} /Users/admin/typesafe-computer-use/.venv/bin/python - <<EOF || true
from typesafe_computer_use.worker.db import WorkerDatabase
import urllib.request, json

db = WorkerDatabase("/Users/admin/typesafe-computer-use/worker.db")
task = db.get_task("${TASK_ID}")
if task and task.state.value == "awaiting_review":
    events = db.get_events("${TASK_ID}")
    rev_events = [e for e in events if str(e.phase) == "state_changed" and e.state.value == "awaiting_review"]
    if rev_events:
        rev = rev_events[0]
        appr_url = "http://127.0.0.1:${WORKER_PORT}/tasks/${TASK_ID}/approvals/" + rev.event_id
        resume_url = "http://127.0.0.1:${WORKER_PORT}/tasks/${TASK_ID}/resume"
        headers = {"Authorization": "Bearer ${AUTH_TOKEN}", "Content-Type": "application/json"}
        req_body = json.dumps({
            "step": rev.step,
            "action": rev.action,
            "target": rev.target or "",
            "screenshot_hash": rev.extra.get("screenshot_hash", ""),
            "action_fingerprint": rev.extra.get("action_fingerprint", "")
        }).encode("utf-8")
        req = urllib.request.Request(appr_url, data=req_body, headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            pass
        req_resume = urllib.request.Request(resume_url, data=b"{}", headers=headers, method="POST")
        with urllib.request.urlopen(req_resume) as resp:
            pass
EOF
    fi

    if [[ "${STATE}" == "stopped" || "${STATE}" == "failed" || "${STATE}" == "succeeded" || "${STATE}" == "done" ]]; then
        echo "Task reached terminal state: ${STATE}"
        break
    fi
    sleep 3
done

# 7. Query SQLite database inside Guest to verify Phase 2C Hybrid Telemetry
echo "[7/8] Verifying Phase 2C Hybrid Telemetry in Guest SQLite database..."
${SSH_CMD} /Users/admin/typesafe-computer-use/.venv/bin/python - <<EOF
from typesafe_computer_use.worker.db import WorkerDatabase
import sys

db = WorkerDatabase("/Users/admin/typesafe-computer-use/worker.db")
events = db.get_events("${TASK_ID}")
print(f"Retrieved {len(events)} events for task ${TASK_ID}")

# Print summary of all events
for e in events:
    print(f"  step={e.step} phase={e.phase} action={e.action} router_mode={e.router_mode} executed_mode={e.executed_mode}")

exec_evts = [e for e in events if getattr(e.phase, "value", str(e.phase)) == "action_executed"]
assert exec_evts, f"No action_executed events found! Total events: {[e.phase for e in events]}"

for evt in exec_evts:
    print(f"Verified action_executed event: id={evt.event_id}, action={evt.action}")
    print(f"  router_mode: {evt.router_mode}")
    print(f"  selected_mode: {evt.selected_mode}")
    print(f"  executed_mode: {evt.executed_mode}")
    print(f"  fallback_from: {evt.fallback_from}")
    print(f"  fallback_count: {evt.fallback_count}")
    print(f"  side_effect_state: {evt.side_effect_state}")
    assert evt.router_mode == "hybrid", f"Expected router_mode=hybrid, got {evt.router_mode}"
    assert evt.executed_mode in ("browser_dom", "visual_grounded"), f"Invalid executed_mode: {evt.executed_mode}"

print("Hybrid telemetry assertions PASSED.")
EOF

# 8. Verify live screenshot retrieval over HTTP and Safety Gates (Takeover / Emergency Stop)
echo "[8/8] Verifying Screenshot retrieval and Safety Controls in Hybrid Mode..."
TMP_IMG=$(mktemp /tmp/test_tart_hybrid_XXXXXX.png)
HTTP_CODE=$(curl -s -o "${TMP_IMG}" -w "%{http_code}" "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/screenshot" -H "${AUTH_HEADER}")
if [[ "${HTTP_CODE}" -ne 200 ]]; then
    echo "ERROR: Screenshot retrieval returned HTTP ${HTTP_CODE}" >&2
    exit 1
fi
python3 -c "
from PIL import Image
im = Image.open('${TMP_IMG}')
print(f'Retrieved screenshot verified: {im.size[0]}x{im.size[1]} {im.format} ({im.mode})')
assert im.size[0] >= 800 and im.size[1] >= 600, f'Invalid screenshot dimensions: {im.size}'
"
rm -f "${TMP_IMG}"
echo "Screenshot verified."

# Test Human Takeover in Hybrid Mode
echo "Testing Human Takeover in Hybrid Mode..."
# Pause first
curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/pause" -H "${AUTH_HEADER}" >/dev/null || true
sleep 1

TAKEOVER_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/takeover" -H "${AUTH_HEADER}")
echo "Takeover response: ${TAKEOVER_RESP}"
python3 -c "
import json
data = json.loads('''${TAKEOVER_RESP}''')
assert data.get('status') == 'takeover', f'Unexpected status: {data}'
assert data.get('input_locked') is True, f'Input not locked: {data}'
print('Takeover verified with input lock in Hybrid mode.')
"

# Verify input lock file on guest
${SSH_CMD} "test -f /tmp/typesafe_input_locked"
echo "Guest input lock confirmed engaged."

# Release Takeover
curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/release-takeover" -H "${AUTH_HEADER}" >/dev/null
sleep 1

# Emergency Stop
echo "Testing Emergency Stop in Hybrid Mode..."
ESTOP_RESP=$(curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/emergency-stop" -H "${AUTH_HEADER}")
echo "Emergency stop response: ${ESTOP_RESP}"
python3 -c "
import json
data = json.loads('''${ESTOP_RESP}''')
assert data.get('input_locked') is True, f'Input not locked after estop: {data}'
print('Emergency stop confirmed input_locked=True in Hybrid mode.')
"

# Reset task
curl -s -X POST "http://${VM_IP}:${WORKER_PORT}/tasks/${TASK_ID}/reset" -H "${AUTH_HEADER}" >/dev/null
sleep 1
${SSH_CMD} "test ! -f /tmp/typesafe_input_locked"
echo "Input lock cleanly cleared after reset."

echo "=========================================================="
echo " Phase 2C Structured Hybrid Execution E2E PASSED 100%!"
echo "=========================================================="
