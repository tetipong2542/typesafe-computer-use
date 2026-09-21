# Phase 2C Structured Hybrid Execution E2E Evidence & Acceptance Report

**Execution Date**: 2026-09-21  
**Target Environment**: macOS Sonoma 14.5 (Guest VM `macos-worker` on Apple Silicon arm64)  
**Python Runtime**: Python 3.13.12 (`/Users/admin/typesafe-computer-use/.venv/bin/python`)  
**Rollout Mode Tested**: `INTERACTION_ROUTER_MODE=hybrid`  
**Checkpoint Created**: `macos-worker-phase2c-api-checkpoint` (Tart APFS Snapshot)

---

## 1. Acceptance Matrix Summary (Pure API-Driven E2E)

| Requirement | Test Suite / Evidence | Result | Notes |
|---|---|---|---|
| **API-Driven Task Creation** | `POST /tasks` via HTTP | **PASS** | Created `task_20260921-035641_627bc3`, `run_id=run_20260921-035641_5e0ce70f`. |
| **Direct Live DOM Execution** | Worker Supervisor -> Step Runner -> Router -> `BrowserDOMAdapter` | **PASS** | Step 1 clicked `#submit-btn`. Mutated `#status` to `Submitted:` in live Headful Chrome. `side_effect_state=confirmed_success`, `fallback_count=0`. |
| **Automatic Fallback on Clean Failure** | Router -> `BrowserDOMAdapter` (fail) -> `VisualComputerUseAdapter` | **PASS** | Step 2 target `Apple` not found in DOM (`ElementNotFoundError`). Clean `CONFIRMED_FAILURE` before mutation. Safe fallback to real macOS AXUIElement click (`fallback_count=1`). |
| **Zero Synthetic Stub / Real Adapters** | Live macOS & Playwright CDP | **PASS** | Zero synthetic test-only stub adapters in acceptance path. Real `BrowserDOMAdapter` and real `VisualComputerUseAdapter`. |
| **Zero Direct DB Injections** | Worker Service Event Loop | **PASS** | All events written strictly by `WorkerService` task loop. Test driver interacted strictly via HTTP API (`POST /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/events`). |
| **Cross-Feed Consistency** | `GET /tasks/{id}` vs SSE vs SQLite | **PASS** | 10 events in SQLite match 10 events received in realtime SSE feed. Task status matches across all endpoints. |
| **Full Host Regression** | `uv run pytest` (Host) | **PASS (270/270)** | 270 passed in 23.75s across all 20 test modules. |
| **Full Guest Regression** | `uv run pytest` (Guest VM) | **PASS (270/270)** | 270 passed in 50.00s natively inside `macos-worker`. |
| **Tart VM Final Checkpoint** | `tart clone` | **PASS** | `macos-worker-phase2c-api-checkpoint` verified in Tart registry. |

---

## 2. Telemetry Extract from Live API Run (`task_20260921-035641_627bc3`)

### Step 1: Direct DOM Mutation
```json
{
  "step": 1,
  "phase": "action_executed",
  "action": "click_item",
  "target": "Submit Query",
  "extra": {
    "selected_mode": "browser_dom",
    "executed_mode": "browser_dom",
    "fallback_from": null,
    "fallback_to": null,
    "fallback_count": 0,
    "router_mode": "hybrid",
    "side_effect_state": "confirmed_success",
    "adapter": "BrowserDOMAdapter"
  }
}
```

### Step 2: Safe Automatic Fallback to Visual Core
```json
{
  "step": 2,
  "phase": "action_executed",
  "action": "click_item",
  "target": "Apple",
  "extra": {
    "selected_mode": "browser_dom",
    "executed_mode": "visual_grounded",
    "fallback_from": "browser_dom",
    "fallback_to": "visual_grounded",
    "fallback_reason": "Element not found matching target: Apple",
    "fallback_count": 1,
    "router_mode": "hybrid",
    "side_effect_state": "confirmed_failure",
    "adapter": "VisualComputerUseAdapter"
  }
}
```

---

## 3. Raw Evidence Artifacts

1. **`api-task-create-response.json`**: HTTP response from `POST /tasks`.
2. **`api-task-final-status.json`**: HTTP response from `GET /tasks/{task_id}` upon completion.
3. **`api-task-sse-events.json`**: Realtime SSE event feed captured from `GET /tasks/{task_id}/events`.
4. **`api-task-sqlite-events.json`**: Dump of SQLite rows from `worker.db` on the Guest VM.
5. **`api-e2e-screenshot.png`**: Screenshot downloaded via `GET /tasks/{task_id}/screenshot` showing `#status` mutated in Chrome and `Submit Query` bounding box.
6. **`guest-pytest-270-output.txt`**: Full regression test output from Guest VM (270/270 passed).
7. **Tart Checkpoint**: `macos-worker-phase2c-api-checkpoint`.
