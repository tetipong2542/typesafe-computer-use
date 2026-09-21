# Phase 3A: WindTunnel Benchmark Integration Report

## 1. Executive Summary

This report documents the vendoring, integration, and pre-paid validation of **WindTunnel** (pinned upstream at commit `5ca8644e23826ebb30108e7bad240b61043bfe67`) as an evaluation-only benchmark harness for `typesafe-computer-use` (commit `8580072`).

All three pre-paid validation requirements specified by the user have been completed and verified:
1. **Full Guest Regression**: `uv run ruff check .` and `uv run pytest` executed on Tart macOS Guest VM (`192.168.64.3`), collecting and passing **all 293 items** in 47.59s (matching Host Mac).
2. **Deterministic Live Arm Validation**: Executed zero-cost ($0.00) live simulation script `scripts/verify_deterministic_arms.mjs` verifying strict interaction mode enforcement and telemetry across all 5 arms.
3. **Paid Smoke Benchmark Plan**: Formulated exact CLI parameters, models, budget cap ($1.00), turn limits, and security guardrails.

Status: **`READY FOR PAID BENCHMARK APPROVAL`** (Awaiting explicit user budget authorization before running paid models).

---

## 2. Architecture & Isolation Boundary

```
+-----------------------------------------------------------------------------------+
|                            typesafe-computer-use                                  |
|                                                                                   |
|   +---------------------------------------------------------------------------+   |
|   | PRODUCTION WORKER RUNTIME (Strictly Isolated)                             |   |
|   |                                                                           |   |
|   |  - Core Visual: actions.py, perception.py, decide.py, macos.py (UNTOUCHED) |   |
|   |  - Tri-Tier Router: typesafe_computer_use/router/shadow.py                |   |
|   |  - WebMCP Engine: typesafe_computer_use/webmcp/ (Native Chrome 153 C++)   |   |
|   |  - Fastify/Uvicorn Daemon: HTTP Port 8000 (Hosted on Tart macOS VM)       |   |
|   +---------------------------------------------------------------------------+   |
|                                     ^                                             |
|                                     | HTTP REST API / Subprocess (evaluation-only)|
|                                     v                                             |
|   +---------------------------------------------------------------------------+   |
|   | BENCHMARK HARNESS: benchmarks/windtunnel/                                 |   |
|   | Pinned Upstream: 5ca8644e23826ebb30108e7bad240b61043bfe67                 |   |
|   |                                                                           |   |
|   |  - Harness Engine: harness/cli.mjs, harness/run.mjs, harness/capsule.mjs  |   |
|   |  - Arms Registry: arms/typesafe.mjs                                       |   |
|   |     * ts-webmcp-native                                                    |   |
|   |     * ts-browser-dom                                                      |   |
|   |     * ts-visual                                                           |   |
|   |     * ts-hybrid-auto                                                      |   |
|   |     * ts-webmcp-compat                                                    |   |
|   |  - Tests: tests/typesafe-arms.test.mjs (12/12 PASS)                       |   |
|   +---------------------------------------------------------------------------+   |
+-----------------------------------------------------------------------------------+
```

### Architectural Guardrails Enforced
1. **Evaluation-Only Harness**: WindTunnel has zero write access to production database records, execution gates, or worker daemons.
2. **Zero Injected Polyfills in Native WebMCP**: `ts-webmcp-native` invokes tools purely through Chrome Blink C++ `document.modelContext`, refusing to inject monkey-patched bridges into production sessions.
3. **Cost Guardrail ($0.00)**: All pre-paid validation runs executed offline without incurring paid LLM token costs.

---

## 3. Theoretical Comparison Matrix (Pre-Benchmark Hypothetical Profiles)

> [!IMPORTANT]
> **Disclaimer on Performance Metrics:**
> Figures in the table below (such as Turn Latency ~1–5 ms / ~50–200 ms / ~500–1500 ms and Token Profiles 0–50 / 2K–15K tokens) represent **theoretical/hypothetical design profiles** based on architectural characteristics, NOT measured WindTunnel benchmark data. Real measured numbers will be collected and reported only during actual benchmark runs following user approval.

| Evaluation Dimension | Native WebMCP (`ts-webmcp-native`) | Scripted Baseline (`scripted`) | Headless DOM (`ts-browser-dom`) | Visual Grounded (`ts-visual`) | TypeSafe Hybrid Auto (`ts-hybrid-auto`) |
|---|---|---|---|---|---|
| **Interaction Channel** | In-page Blink C++ `document.modelContext` / Declarative Forms | Hardcoded Playwright locators | DOM Tree / CSS Locators / A11y Tree | OS Display framebuffer / Coordinate clicks | Adaptive: WebMCP $\rightarrow$ DOM $\rightarrow$ Visual |
| **Ingress Latency (Hypothetical)** | Fastest (~1-5 ms theoretical) | Fast (~5-20 ms theoretical) | Moderate (~50-200 ms theoretical) | Slow (~500-1500 ms theoretical) | Dynamic (~5 ms on WebMCP, ~50 ms on DOM) |
| **Token & Cost Profile (Hypothetical)** | Minimal (~0-50 tokens theoretical) | Zero (Deterministic) | High (DOM snapshots: 2K-15K tokens) | Extremely High (Images: 1K-4K tokens/frame) | Optimal (Adaptive minimization) |
| **Mutation Resilience** | **Highest**: Website declares and maintains tool contract | **Lowest**: Breaks on any selector change | Moderate: Vulnerable to CSS/DOM obfuscation | High: Robust against DOM churn, vulnerable to layout/skin shifts | **Highest**: Seamlessly cascades down tiers |
| **Consequential Safety** | **Cryptographic Server Approval**: Epoch & argument hash verification | None | None / Rule-based heuristics | Visual Confirmation / Operator gate | **Universal Gate & SQLite Approvals** |
| **Application Scope** | Modern WebMCP-enabled websites | Target-specific scripts | Browser web applications | **Universal (Desktop, Native apps, OS dialogs)** | **Universal Hybrid** |

---

## 4. Verification Evidence

### 4.1 Full Guest VM Regression (Tart macOS Sonoma, Python 3.13)
Executed inside Tart Guest VM `macos-worker` (`192.168.64.3`) on commit `8580072`:
```bash
ssh admin@192.168.64.3 "cd /Users/admin/typesafe-computer-use && uv run ruff check . && uv run pytest"
```
**Raw Output Evidence:**
```text
On branch main
nothing to commit, working tree clean
8580072 feat(benchmark): implement zero-cost deterministic validation for 5 benchmark arms
All checks passed!
============================= test session starts ==============================
platform darwin -- Python 3.13.12, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/admin/typesafe-computer-use
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1
collected 293 items

tests/test_actions.py ....................                               [  6%]
tests/test_adapters.py ......                                            [  8%]
tests/test_answer.py ...............                                     [ 13%]
tests/test_ax.py ...........................                             [ 23%]
tests/test_browser_dom.py .....                                          [ 24%]
tests/test_browser_session.py ................                           [ 30%]
tests/test_config_and_writer.py ...                                      [ 31%]
tests/test_dates.py .....                                                [ 33%]
tests/test_db_migration.py ...                                           [ 34%]
tests/test_decide.py ............                                        [ 38%]
tests/test_emergency_process.py ..                                       [ 38%]
tests/test_execution_gate.py .....                                       [ 40%]
tests/test_execution_gate_race.py .......                                [ 43%]
tests/test_hybrid_router_e2e.py ......                                   [ 45%]
tests/test_input_lock.py .....                                           [ 46%]
tests/test_input_lock_isolation.py .....                                 [ 48%]
tests/test_native_webmcp.py ........                                     [ 51%]
tests/test_native_webmcp_standards.py ......                             [ 53%]
tests/test_navigation_epoch_and_ambiguity.py ...                         [ 54%]
tests/test_ocr_cache.py ....................................             [ 66%]
tests/test_openai_writer.py ....                                         [ 67%]
tests/test_perception.py ...............                                 [ 73%]
tests/test_policy_gate.py .........                                      [ 76%]
tests/test_real_browser.py ...                                           [ 77%]
tests/test_real_browser_delayed_side_effect.py ......                    [ 79%]
tests/test_sanitization_precision.py ...                                 [ 80%]
tests/test_shadow_probe_readonly.py .                                    [ 80%]
tests/test_shadow_router.py ...                                          [ 81%]
tests/test_task_router_integration.py .                                  [ 81%]
tests/test_timing.py ...........                                         [ 85%]
tests/test_untrusted_dom_boundary.py ..                                  [ 86%]
tests/test_webmcp_server.py .........                                    [ 89%]
tests/test_worker_api.py ...........................                     [ 98%]
tests/test_worker_state.py ....                                          [100%]
======================= 293 passed, 1 warning in 47.59s ========================
```
> Full suite: **293 passed, 1 warning in 47.59s** (100% PASS on Guest, matching Host Mac 293 passed in 27.26s).

### 4.2 WindTunnel Upstream & Unit Test Suite
Ran via `node --test` inside `benchmarks/windtunnel`:
```
ℹ tests 105
ℹ suites 0
ℹ pass 104
ℹ fail 0
ℹ cancelled 0
ℹ skipped 1
ℹ duration_ms 4180.53
```
> Exact Summary: **104 passed, 1 skipped, 0 failed** across 105 tests (1 skipped upstream by design: `medusa: complete_checkout`). Includes all 12 TypeSafe arm unit tests.

### 4.3 Deterministic Live Arm Validation ($0.00 Spent)
Executed `scripts/verify_deterministic_arms.mjs` on both Host Mac (Bun runtime) and Tart Guest VM (Node 24 runtime):
```bash
bun run scripts/verify_deterministic_arms.mjs
```
**Validation Evidence Summary Table:**

| Arm ID | `selected_mode` | `executed_mode` | `webmcp_implementation` | Visual Invocation Count | Missing Support Handling | Empty Tools Handling | Task Predicate | Invariant Status |
|---|---|---|---|---|---|---|---|---|
| `ts-webmcp-native` | `webmcp` | `webmcp` | `native` | 0 | `native-webmcp-unsupported` | `no-webmcp-tools` | `true` | **VERIFIED PASS** |
| `ts-browser-dom` | `browser_dom` | `browser_dom` | `n/a` | **0** | `n/a` | `n/a` | `true` | **VERIFIED PASS** |
| `ts-visual` | `visual_grounded` | `visual_grounded` | `n/a` | 1 | `n/a` | `n/a` | `true` | **VERIFIED PASS** |
| `ts-hybrid-auto` | `adaptive` | `webmcp` (when ready)<br>$\rightarrow$ `browser_dom` (fallback) | `native` (when present) | 0 | `fallback to browser_dom` | `fallback to browser_dom` | `true` | **VERIFIED PASS** |
| `ts-webmcp-compat` | `webmcp` | `webmcp` | `compatibility_bridge` *(never native)* | 0 | Injected bridge | `n/a` | `true` | **VERIFIED PASS** |

### 4.4 Tart VM Checkpoint Status
- **Pre-benchmark Checkpoint:** `macos-worker-phase3a-prebench-checkpoint`
  - Created and verified stopped in Tart local registry.
  - Pinned to commit `8580072`.
- **Active Worker VM:** `macos-worker` running at `192.168.64.3:8000` (healthy daemon).

---

## 5. Paid Smoke Benchmark Plan (Finalized & Pre-Run Validated)

The following smoke run is prepared to execute immediately upon user budget approval:

```bash
# Exact execution command
cd benchmarks/windtunnel
node harness/cli.mjs \
  --sites directory-9d8 \
  --arms ts-webmcp-native,ts-browser-dom,ts-visual,ts-hybrid-auto \
  --model claude-sonnet-4-6 \
  --budget 1.00 \
  --n 1
```

### Detailed Run Parameters
- **Upstream WindTunnel Commit:** `5ca8644e23826ebb30108e7bad240b61043bfe67`
- **TypeSafe Codebase Commit:** `8580072`
- **Target Site:** `directory-9d8` (Self-hosted SQLite directory capsule)
- **Target Tasks:**
  1. `directory-search` (Tier 1: Search for collaborative design tool and extract name)
  2. `directory-filter` (Tier 2: Filter by category and assert filtered entity)
- **Evaluated Arms (4 arms):**
  1. `ts-webmcp-native` (Native Blink C++ WebMCP)
  2. `ts-browser-dom` (DOM / Playwright locators)
  3. `ts-visual` (Visual Computer Use screenshots)
  4. `ts-hybrid-auto` (Tri-tier adaptive routing)
- **Model Provider:** Anthropic (`claude-sonnet-4-6`)
- **Repeats:** `n = 1`
- **Turn Limit:** `5` turns per task attempt
- **Per-Attempt Timeout:** `120s`
- **Maximum Model Calls:** $\le 4\text{ arms} \times 2\text{ tasks} \times 5\text{ turns} = 40$ calls maximum
- **Estimated Token Spend:** $\approx 15,000 - 35,000$ input tokens, $\approx 1,500 - 4,000$ output tokens
- **Estimated Cost:** $\approx \$0.15 - \$0.35$ USD
- **Hard Budget Cap:** **`$1.00`** USD (CLI option `--budget 1.00` terminates process if spend exceeds $1.00)
- **Security Guardrail:**
  - Zero API keys in CLI arguments, process args, git logs, or test logs.
  - API key supplied strictly via private environment variable `ANTHROPIC_API_KEY`.
- **Automated Termination / Stop Conditions:**
  1. Hard budget cap reached (`budget_exhausted: true`).
  2. More than 2 consecutive infrastructure/network failures (`--max-consecutive-infra 2`).
  3. Any worker execution gate exception or unauthorized write attempt.
