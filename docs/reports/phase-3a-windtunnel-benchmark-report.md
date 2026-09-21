# Phase 3A: WindTunnel Benchmark Integration Report

## 1. Executive Summary

This report documents the vendoring, integration, and verification of **WindTunnel** (pinned upstream at commit `5ca8644e23826ebb30108e7bad240b61043bfe67`) as an evaluation-only benchmark harness for `typesafe-computer-use`.

Five dedicated benchmark arms were implemented and verified with zero LLM API cost under `WT_FAKE_LIFECYCLE=1`, maintaining strict architectural isolation and keeping the production visual core runtime (`actions.py`, `perception.py`, `decide.py`, `macos.py`) completely intact.

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
|   |  - Tests: tests/typesafe-arms.test.mjs (98/98 PASS)                       |   |
|   +---------------------------------------------------------------------------+   |
+-----------------------------------------------------------------------------------+
```

### Architectural Guardrails Enforced
1. **Evaluation-Only Harness**: WindTunnel has zero write access to production database records, execution gates, or worker daemons.
2. **Zero Injected Polyfills in Native WebMCP**: `ts-webmcp-native` invokes tools purely through Chrome Blink C++ `document.modelContext`, refusing to inject monkey-patched bridges into production sessions.
3. **Cost Guardrail ($0.00)**: All smoke and baseline runs execute offline or under `WT_FAKE_LIFECYCLE=1` without incurring paid LLM token costs.

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

## 4. Verification & Test Evidence

### 4.1 WindTunnel Test Suite
Ran via `npm test` inside `benchmarks/windtunnel`:
```
ℹ tests 102
ℹ suites 0
ℹ pass 101
ℹ fail 0
ℹ cancelled 0
ℹ skipped 1
ℹ duration_ms 4188.79
```
> Exact Summary: **101 passed, 1 skipped, 0 failed** across 102 tests (1 skipped upstream by design: `medusa: complete_checkout`).

### 4.2 Arms Self-Check
```bash
WT_TYPESAFE_WORKER_URL=http://192.168.64.3:8000 bun scripts/arms-selfcheck.mjs
```
Output:
```json
{
  "typesafe-computer-use": {
    "ok": true,
    "framework": "typesafe-computer-use",
    "version": "typesafe-computer-use@0.2.0",
    "worker_url": "http://192.168.64.3:8000",
    "worker_online": true,
    "arms": [
      "ts-webmcp-native",
      "ts-browser-dom",
      "ts-visual",
      "ts-hybrid-auto",
      "ts-webmcp-compat"
    ]
  }
}
```

### 4.3 Fake Lifecycle Dry Run (Scope & Boundaries)
```bash
WT_FAKE_LIFECYCLE=1 bun harness/cli.mjs --preset smoke --sites lite \
  --arms ts-webmcp-native,ts-browser-dom,ts-visual,ts-hybrid-auto,ts-webmcp-compat,scripted --n 1
```
Output:
- Report generated at `benchmarks/windtunnel/results/dry-2026-09-21-lite-smoke/report.md`
- Total Cost: **$0.0000**
- Combinations: 42 task $\times$ method combinations evaluated.

> [!NOTE]
> **Dry Run Scope & Limitations:**
> The `WT_FAKE_LIFECYCLE=1` dry run proves strictly that:
> - The WindTunnel harness loads and initializes without syntax/import errors.
> - All 5 TypeSafe arms register into the CLI and dispatch correctly.
> - The task lifecycle, reporting pipeline, and result schemas are fully compatible.
> - Zero LLM API calls are made and cost is guaranteed $0.0000.
>
> It does **NOT** prove:
> - Real task completion or end-to-end success against live websites.
> - Actual worker execution under high load.
> - Real DOM, WebMCP, or Visual action execution.
> - Empirical latency, token usage, cost, or real error recovery/fallback performance.

---

## 5. Proposed Paid Smoke Benchmark Plan (Draft)

To measure real empirical performance across interaction paradigms, the following smoke benchmark plan is proposed for user review and approval:

- **Harness Upstream Commit:** `5ca8644e23826ebb30108e7bad240b61043bfe67`
- **TypeSafe Commit:** `e8d6411` (or latest HEAD)
- **Target Site:** `directory-9d8` (lightweight directory site with searchable UI)
- **Task IDs:**
  1. `directory-search` (Search directory for a design tool and report name)
  2. `directory-filter` (Filter directory to developer tools and report result)
- **Evaluated Arms:**
  1. `ts-webmcp-native`
  2. `ts-browser-dom`
  3. `ts-visual`
  4. `ts-hybrid-auto`
- **Model / Provider:** `claude-sonnet-4-6` (via Anthropic API) or `gpt-5.5` (OpenAI)
- **Model Snapshot:** Fixed provider snapshot pinned at runtime
- **Configuration:**
  - Repeats: `n = 1`
  - Seed: `1`
  - Turn Limit: `5` turns per task attempt
  - Timeout: `120s` per attempt
  - Max Model Invocations: $\le 4\text{ arms} \times 2\text{ tasks} \times 5\text{ turns} = 40$ calls maximum
- **Cost Estimation:**
  - Estimated cost per arm: $\approx \$0.02 - \$0.06$
  - Estimated total cost: $\approx \$0.15 - \$0.30$
- **Budget Hard Cap:** **`$1.00`** (Enforced by `--budget 1.00` in WindTunnel CLI)
- **Security Guardrail:** API keys read strictly from environment variable `ANTHROPIC_API_KEY` (never passed in CLI args or logged).
- **Automated Stop Conditions:**
  - Hard budget cap of $\$1.00$ reached.
  - More than 2 consecutive provider infrastructure failures (`--max-consecutive-infra 2`).
  - Any fatal worker execution gate violation.
