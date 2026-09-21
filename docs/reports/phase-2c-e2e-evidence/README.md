# Phase 2C Structured Hybrid Execution E2E Evidence & Acceptance Report

**Execution Date**: 2026-09-21  
**Target Environment**: macOS Sonoma 14.5 (Guest VM `macos-worker` on Apple Silicon arm64)  
**Python Runtime**: Python 3.13.12 (`/Users/admin/typesafe-computer-use/.venv/bin/python`)  
**Rollout Mode Tested**: `INTERACTION_ROUTER_MODE=hybrid`  
**Checkpoint Created**: `macos-worker-phase2c-checkpoint` (Tart APFS Snapshot)

---

## 1. Acceptance Matrix Summary

| Requirement | Test Suite / Evidence | Result | Notes |
|---|---|---|---|
| **Direct DOM Execution** | `tests/test_hybrid_router_e2e.py::test_hybrid_direct_dom_execution` | **PASS** | `selected_mode=browser_dom`, `executed_mode=browser_dom`, visual adapter uncalled. |
| **Automatic Safe Fallback** | `tests/test_hybrid_router_e2e.py::test_hybrid_automatic_fallback_on_confirmed_failure` | **PASS** | On `CONFIRMED_FAILURE` (missing DOM element), falls back to `VISUAL_GROUNDED`, tracks reason & increments count. |
| **Strict Side-Effect Protection** | `tests/test_hybrid_router_e2e.py::test_hybrid_strict_block_on_unknown_side_effect` | **PASS** | On `UNKNOWN` side-effect state, automatic retry/fallback is **strictly forbidden**. Escalates to review. |
| **Mixed-Mode Tasks** | `tests/test_hybrid_router_e2e.py::test_hybrid_mixed_mode_task_routing` | **PASS** | Web context routes to `BROWSER_DOM`; desktop Finder/OS control routes directly to `VISUAL_GROUNDED`. |
| **Action Translation** | `tests/test_hybrid_router_e2e.py::test_hybrid_action_translations` | **PASS** | Translates high-level actions (`click_item` -> `click`, `use_browser` -> `navigate`, `type_text` -> `fill`, etc.). |
| **Full Host Regression** | `uv run pytest` (Host) | **PASS (270/270)** | 0 failures, 0 regressions across all 20 test modules. |
| **Full Guest Regression** | `uv run pytest` (Guest VM) | **PASS (270/270)** | Executed via native Python 3.13.12 inside `macos-worker` (`guest-pytest-270-output.txt`). |
| **Tart VM E2E Live Run** | `./scripts/e2e-tart-hybrid.sh` | **PASS (100%)** | Full lifecycle: task creation, execution, telemetry persistence, screenshot HTTP download, takeover & emergency stop. |
| **Safety Invariants** | Live Takeover & Emergency Stop | **PASS** | Synthetic input lock verified engaged during takeover & emergency stop, cleared cleanly upon reset. |

---

## 2. Telemetry Schema & Persistence

Every task execution under `INTERACTION_ROUTER_MODE=hybrid` emits enriched telemetry stored in SQLite `events` and inspectable via the Worker API:

- **`router_mode`**: Active router configuration (`"hybrid"`).
- **`selected_mode`**: Planned interaction adapter chosen before execution (`"browser_dom"` or `"visual_grounded"`).
- **`executed_mode`**: Final interaction adapter that completed the action (`"browser_dom"` or `"visual_grounded"`).
- **`fallback_from`**: Initial mode if fallback occurred (`"browser_dom"`).
- **`fallback_to`**: Fallback destination mode (`"visual_grounded"`).
- **`fallback_reason`**: Detailed error description explaining why fallback took place.
- **`fallback_count`**: Cumulative number of fallbacks recorded for the task.
- **`side_effect_state`**: State mutation certainty (`"not_started"`, `"confirmed_success"`, `"confirmed_failure"`, or `"unknown"`).

### Verified SQLite Event Row Sample (`task_20260921-030849_7eadfe`)
```json
{
  "event_id": "evt_task_20260921-030849_7eadfe_000006",
  "step": 1,
  "phase": "action_executed",
  "action": "use_browser",
  "router_mode": "hybrid",
  "selected_mode": "visual_grounded",
  "executed_mode": "visual_grounded",
  "fallback_from": null,
  "fallback_count": 0,
  "side_effect_state": "not_started"
}
```

---

## 3. Raw Evidence Artifacts

1. **`e2e-hybrid-output.txt`**: Complete terminal transcript from executing `./scripts/e2e-tart-hybrid.sh macos-worker`.
2. **`guest-pytest-270-output.txt`**: Test run output executing all 270 unit and integration tests inside the Tart macOS Guest VM.
3. **`task-events-hybrid-sqlite.json`**: Dump of raw SQLite event rows from `worker.db` inside the Guest VM verifying hybrid fields.
4. **`step-001.png` & `step-002.png`**: Raw annotated screenshots captured and verified inside the Guest VM during live execution.
5. **Tart APFS Checkpoint**: Snapshot created and registered in `tart list` as `macos-worker-phase2c-checkpoint`.
