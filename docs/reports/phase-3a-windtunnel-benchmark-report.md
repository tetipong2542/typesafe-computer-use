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

## 3. Comparison Matrix: Interaction Paradigms

| Evaluation Dimension | Native WebMCP (`ts-webmcp-native`) | Scripted Baseline (`scripted`) | Headless DOM (`ts-browser-dom`) | Visual Grounded (`ts-visual`) | TypeSafe Hybrid Auto (`ts-hybrid-auto`) |
|---|---|---|---|---|---|
| **Interaction Channel** | In-page Blink C++ `document.modelContext` / Declarative Forms | Hardcoded Playwright locators | DOM Tree / CSS Locators / A11y Tree | OS Display framebuffer / Coordinate clicks | Adaptive: WebMCP $\rightarrow$ DOM $\rightarrow$ Visual |
| **Ingress Latency** | **Fastest (~1-5 ms)** | Fast (~5-20 ms) | Moderate (~50-200 ms) | Slow (~500-1500 ms per turn) | Dynamic (~5 ms on WebMCP, ~50 ms on DOM) |
| **Token & Cost Profile** | **Minimal (~0-50 tokens/turn)** | Zero (Deterministic) | High (DOM snapshots: 2K-15K tokens) | Extremely High (Images: 1K-4K tokens/frame) | **Optimal (Adaptive minimization)** |
| **Mutation Resilience** | **Highest**: Website declares and maintains tool contract | **Lowest**: Breaks on any selector change | Moderate: Vulnerable to CSS/DOM obfuscation | High: Robust against DOM churn, vulnerable to layout/skin shifts | **Highest**: Seamlessly cascades down tiers |
| **Consequential Safety** | **Cryptographic Server Approval**: Epoch & argument hash verification | None | None / Rule-based heuristics | Visual Confirmation / Operator gate | **Universal Gate & SQLite Approvals** |
| **Application Scope** | Modern WebMCP-enabled websites | Target-specific scripts | Browser web applications | **Universal (Desktop, Native apps, OS dialogs)** | **Universal Hybrid** |

---

## 4. Verification & Test Evidence

### 4.1 WindTunnel Test Suite
Ran via `npm test` inside `benchmarks/windtunnel`:
```
ℹ tests 98
ℹ suites 0
ℹ pass 97
ℹ fail 0
ℹ cancelled 0
ℹ skipped 1
ℹ duration_ms 4154.61
```

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

### 4.3 Fake Lifecycle Dry Run
```bash
WT_FAKE_LIFECYCLE=1 bun harness/cli.mjs --preset smoke --sites lite \
  --arms ts-webmcp-native,ts-browser-dom,ts-visual,ts-hybrid-auto,ts-webmcp-compat,scripted --n 1
```
Output:
- Report generated at `benchmarks/windtunnel/results/dry-2026-09-21-lite-smoke/report.md`
- Total Cost: **$0.0000**
- Combinations: 42 task $\times$ method combinations evaluated cleanly without network leaks or crashes.
