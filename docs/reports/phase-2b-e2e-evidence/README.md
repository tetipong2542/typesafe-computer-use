# Phase 2B E2E Verification & Acceptance Evidence

This directory contains the primary audit evidence verifying the completion of **Phase 2B (Tart macOS VM E2E Verification)** for `typesafe-computer-use`.

Zero mocks or fixture shortcuts were used for VM E2E verification. All operations were performed against a live macOS Sonoma 14.8.7 guest (`macos-worker`) under Tart virtualization with active Aqua/WindowServer GUI session.

---

## 1. Acceptance Matrix (Phase 1 & Phase 2B)

| ID | Requirement / Test Scope | Criterion | Result | Evidence Reference |
| :--- | :--- | :--- | :---: | :--- |
| **P1-01** | Visual Core & Local Mac Adapter | Synthetic click, type, press, scroll via CGEvent / AX | **PASS** | [`tests/test_actions.py`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/tests/test_actions.py), [`tests/test_ax.py`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/tests/test_ax.py) |
| **P1-02** | Hardware / File Input Lock | Engaging input lock aborts synthetic events | **PASS** | [`tests/test_input_lock.py`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/tests/test_input_lock.py), [`tests/test_input_lock_isolation.py`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/tests/test_input_lock_isolation.py) |
| **P1-03** | Worker HTTP & SSE API Daemon | Endpoints for tasks, pause, resume, stop, estop, reset, takeover | **PASS** | [`tests/test_worker_api.py`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/tests/test_worker_api.py) (27 tests) |
| **P1-04** | Execution Gate & Crash Recovery | Safety gate closes on estop/takeover; rehydrates from DB on boot | **PASS** | [`tests/test_execution_gate.py`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/tests/test_execution_gate.py), [`test_crash_recovery_marks_interrupted`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/tests/test_worker_state.py) |
| **P2B-01** | Tart VM Registry & Base Image | Pinned OCI base image `macos-sonoma-base@sha256:e2ebdfc4...` | **PASS** | [`preflight-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/preflight-output.txt) (Step [1/5]) |
| **P2B-02** | Guest Aqua Session & WindowServer | Live login session with active WindowServer on guest display | **PASS** | [`e2e-script-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/e2e-script-output.txt) (Step [4/9]) |
| **P2B-03** | Google Chrome Provenance Audit | Signed by Google LLC (`EQHXZ8M8AV`), Gatekeeper accepted | **PASS** | Version 153.0.8010.53 universal binary audit |
| **P2B-04** | TCC Permissions & Automation | Accessibility, Screen Recording, AppleEvents Automation for Python 3.13.12 | **PASS** | Live non-blocking AppleScript URL probe (`chrome://newtab/`) |
| **P2B-05** | Per-User LaunchAgent Daemon | Loaded in `gui/$(id -u)/com.typesafe.worker`; auto-starts on login | **PASS** | [`e2e-script-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/e2e-script-output.txt) (Step [5/9]) |
| **P2B-06** | Loopback CDP Port Isolation | Remote debugging strictly on `127.0.0.1`, zero public listeners | **PASS** | [`e2e-script-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/e2e-script-output.txt) (Step [6/9]) |
| **P2B-07** | Headful Chrome & Screenshot API | 1440x900 RGB PNG captured and served over HTTP `GET /tasks/:id/screenshot` | **PASS** | [`step-001.png`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/step-001.png), Step [7/9] |
| **P2B-08** | Shadow DOM Telemetry Invariants | `router_mode: shadow`, `executed_mode: visual_grounded` recorded in SQLite | **PASS** | [`task-events-sqlite.json`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/task-events-sqlite.json), Step [7/9] |
| **P2B-09** | Exclusive Takeover & Sanitized VNC | `vnc://admin@192.168.64.3` (zero secret leaks), port 5900 listening, lock engaged | **PASS** | [`e2e-script-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/e2e-script-output.txt) (Step [8/9]) |
| **P2B-10** | Emergency Stop & Reset Flow | Instant hardware/file lock engagement; reset clears lock & updates DB | **PASS** | [`e2e-script-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/e2e-script-output.txt) (Step [9/9]) |
| **P2B-11** | Full Guest Test Suite (264 tests) | Pinned Python 3.13.12 inside VM passes entire test suite | **PASS** | [`guest-pytest-264-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/guest-pytest-264-output.txt) (264 passed in 66.57s) |
| **P2B-12** | Cold Reboot & Auto-Recovery | LaunchAgent auto-starts on reboot; Chrome process reconnect supported | **PASS** | Cold reboot test log; `macos-worker-phase2b-checkpoint` |
| **P2B-13** | SSH Security Standard | No `StrictHostKeyChecking=no`; uses dedicated `known_hosts_tart` & `accept-new` | **PASS** | Updated [`scripts/e2e-tart-worker.sh`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/scripts/e2e-tart-worker.sh), [`scripts/tart-preflight.sh`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/scripts/tart-preflight.sh) |

---

## 2. Evidence Files in this Directory

1. **[`preflight-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/preflight-output.txt)**:
   - Full raw console output of `./scripts/tart-preflight.sh` demonstrating VM presence, IP resolution, SSH connectivity, macOS metadata, and Screen Sharing (VNC port 5900) readiness.
2. **[`e2e-script-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/e2e-script-output.txt)**:
   - Full raw console output of `./scripts/e2e-tart-worker.sh` running all 9 stages against the live VM.
3. **[`guest-pytest-264-output.txt`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/guest-pytest-264-output.txt)**:
   - Verbose test results of `uv run pytest -v` executed inside the Guest VM on commit `fa11b0a` (264 passed, 1 warning, 0 failures).
4. **[`task-events-sqlite.json`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/task-events-sqlite.json)**:
   - JSON dump of the raw event sequence recorded in SQLite `worker.db` on the guest for task `task_20260921-024942_9d3aa7`. Contains `step_perceived`, `action_evaluating`, and `action_executed` with shadow router telemetry.
5. **[`step-001.png`](file:///Users/ponddev/Desktop/My_Project/Demo/typesafe-computer-use/docs/reports/phase-2b-e2e-evidence/step-001.png)**:
   - Live annotated 1440x900 RGB screenshot captured and retrieved from the guest during task execution via `GET /tasks/:id/screenshot`.

---

## 3. Tart VM Checkpoint Reference

An APFS copy-on-write clone was created on the host immediately following complete verification:

- **Checkpoint VM Name**: `macos-worker-phase2b-checkpoint`
- **State**: `stopped` (preserved base state)
- **Restore Command**:
  ```bash
  tart clone macos-worker-phase2b-checkpoint macos-worker
  ```
