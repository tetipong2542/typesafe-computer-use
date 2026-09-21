# Phase 2C Structured Hybrid Execution E2E Evidence & Acceptance Report

**Execution Date**: 2026-09-21  
**Target Environment**: macOS Sonoma 14.8.7 (Guest VM `macos-worker` on Apple Silicon arm64)  
**Python Runtime**: Python 3.13.12 (`/Users/admin/typesafe-computer-use/.venv/bin/python`)  
**Rollout Mode Tested**: `INTERACTION_ROUTER_MODE=hybrid`  
**Initial Checkpoint**: `macos-worker-phase2c-checkpoint`  
**Final Closure Checkpoint**: `macos-worker-phase2c-final-checkpoint` (Tart APFS Snapshot)

---

## 1. Acceptance Matrix Summary (Phase 2C Closure)

| Requirement | Test Suite / Evidence | Result | Notes |
|---|---|---|---|
| **1. Direct DOM Execution** | `scripts/run_hybrid_closure_e2e.py` (Headful Chrome) | **PASS** | Typed query into `#search-input`, clicked `#submit-btn`. DOM mutation verified: `#status` = `"Submitted: TypeSafe Direct DOM Query"`. Telemetry: `selected_mode=browser_dom`, `executed_mode=browser_dom`, `side_effect_state=confirmed_success`, `fallback_count=0`. |
| **2. Automatic DOM → Visual Fallback** | `scripts/run_hybrid_closure_e2e.py` (Missing Target) | **PASS** | Target `#non-existent-button-xyz` threw `ElementNotFoundError`. Side effect `confirmed_failure`. Fallback to visual core executed cleanly. Telemetry: `selected_mode=browser_dom`, `fallback_from=browser_dom`, `executed_mode=visual_grounded`, `fallback_count=1`, `fallback_reason` recorded. |
| **3. Mixed-Mode Single Task** | `scripts/run_hybrid_closure_e2e.py` (Unified Task ID) | **PASS** | Shared single `task_id` & `run_id`. Step 1: Chrome DOM (`browser_dom`). Step 2: Desktop OS Finder (`visual_grounded`). Event order in SQLite strictly verified. |
| **4. Unknown Side-Effect Safety** | `scripts/run_hybrid_closure_e2e.py` (Ambiguous Mutation) | **PASS** | On `side_effect_state == UNKNOWN`: zero retries, zero visual fallback (`visual_attempted=False`), task escalated to `awaiting_review`. |
| **5. Full Host Regression** | `uv run pytest` (Host) | **PASS (270/270)** | 0 failures, 0 regressions across all 20 test modules in 24.86s. |
| **6. Full Guest Regression** | `uv run pytest` (Guest VM) | **PASS (270/270)** | Executed via native Python 3.13.12 inside `macos-worker` in 45.98s (`guest-pytest-270-output.txt`). |
| **7. Tart VM E2E Live Run** | `./scripts/e2e-tart-hybrid.sh` | **PASS (100%)** | All 9 stages passed: Daemon API, Headful Chrome CDP, SQLite telemetry, HTTP screenshot download, Takeover lock, Emergency Stop, and Closure E2E suite. |
| **8. Final VM Checkpoint** | `tart clone` | **PASS** | `macos-worker-phase2c-final-checkpoint` created and verified in Tart registry. |

---

## 2. Closure Telemetry Verification (From Live SQLite DB)

Direct extract from `docs/reports/phase-2c-e2e-evidence/task-events-hybrid-closure-sqlite.json`:

```json
[
  {
    "task_id": "task_closure_direct_dom_1789960922",
    "step": 1,
    "phase": "action_executed",
    "action": "click",
    "target": "#submit-btn",
    "router_mode": "hybrid",
    "selected_mode": "browser_dom",
    "executed_mode": "browser_dom",
    "fallback_from": null,
    "fallback_to": null,
    "fallback_count": 0,
    "fallback_reason": null,
    "side_effect_state": "confirmed_success",
    "result": "Submitted: TypeSafe Direct DOM Query"
  },
  {
    "task_id": "task_closure_fallback_1789960922",
    "step": 1,
    "phase": "action_executed",
    "action": "click",
    "target": "#non-existent-button-xyz",
    "router_mode": "hybrid",
    "selected_mode": "browser_dom",
    "executed_mode": "visual_grounded",
    "fallback_from": "browser_dom",
    "fallback_to": "visual_grounded",
    "fallback_count": 1,
    "fallback_reason": "Element not found matching target: #non-existent-button-xyz",
    "side_effect_state": "confirmed_success",
    "result": "visual_core_fallback_executed"
  },
  {
    "task_id": "task_closure_mixed_1789960922",
    "step": 1,
    "phase": "action_executed",
    "action": "click",
    "target": "#submit-btn",
    "router_mode": "hybrid",
    "selected_mode": "browser_dom",
    "executed_mode": "browser_dom",
    "side_effect_state": "confirmed_success",
    "result": "DOM click performed"
  },
  {
    "task_id": "task_closure_mixed_1789960922",
    "step": 2,
    "phase": "action_executed",
    "action": "click_item",
    "target": "Finder Window",
    "router_mode": "hybrid",
    "selected_mode": "visual_grounded",
    "executed_mode": "visual_grounded",
    "side_effect_state": "confirmed_success",
    "result": "Desktop action performed"
  },
  {
    "task_id": "task_closure_safety_1789960922",
    "step": 1,
    "phase": "state_changed",
    "action": "click",
    "target": "#payment-submit-btn",
    "router_mode": "hybrid",
    "selected_mode": "browser_dom",
    "executed_mode": "browser_dom",
    "side_effect_state": "unknown",
    "result": "Escalated to awaiting_review due to ambiguous mutation state"
  }
]
```

---

## 3. Raw Evidence Artifacts

1. **`e2e-hybrid-closure-output.txt`**: Complete terminal transcript from executing `./scripts/e2e-tart-hybrid.sh macos-worker` with closure suite.
2. **`guest-pytest-270-output.txt`**: Test run output executing all 270 unit and integration tests inside the Tart macOS Guest VM.
3. **`task-events-hybrid-closure-sqlite.json`**: Dump of SQLite event rows validating all 4 closure requirements.
4. **`closure-step-001-direct-dom.png`**: Raw live screenshot of Chrome page after direct DOM execution (`#status` updated).
5. **Tart APFS Final Checkpoint**: Snapshot created and registered in `tart list` as `macos-worker-phase2c-final-checkpoint`.
