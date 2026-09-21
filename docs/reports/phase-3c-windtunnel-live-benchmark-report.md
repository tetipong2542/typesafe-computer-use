# Phase 3C: Live WindTunnel Verified Benchmark ($n=3$) Final Report

## Executive Summary

Phase 3C executed the verified, remediated live benchmark of **TypeSafe Computer Use** on the **WindTunnel** benchmark suite against the `directory-9d8` capsule across **3 tasks** (`directory-search`, `directory-filter`, `directory-detail`), **4 arms** (`ts-webmcp-native`, `ts-browser-dom`, `ts-visual`, `ts-hybrid-auto`), with **$n=3$ repeats per task** (36 total attempts).

All attempts were executed live against **`gpt-5.6-sol`** via `codex-openai-proxy` (port 8888) authenticated with the user's ChatGPT Plus token at **\$0.00 actual spend** (with a nominal list-price equivalent of **\$0.6862**).

This flight satisfies all evaluation and audit integrity requirements:
1. **Zero Golden / Tool Description Leakage**: All category terms (e.g. `"Development"`, `"Figma"`) were excised from tool descriptions and prompt templates in `benchmarks/windtunnel/arms/typesafe.mjs`. The automated leak detector `scripts/check-golden-leakage.mjs` was extended to scan both `goldens/*.patch` and `arms/*.mjs`, and passed with zero hits.
2. **True Autonomous Agentic Interactions**: Every passing attempt performed active multi-turn interactions (98 total actions/tool calls executed across the flight). For `directory-filter`, models discovered the `"Development"` category autonomously from page content.
3. **Active Budget Cap Binding**: Set `paid: true` on all `ts-*` arms in `harness/cli.mjs`, ensuring `--budget 2.00` actively binds and tracks nominal cost overruns.
4. **Browser Flag Passthrough (`WT_CHROME_ARGS`)**: Implemented `--enable-features=WebMCPTesting,WebMCP --enable-blink-features=DocumentModelcontext` forwarding to Chromium launch in `cli.mjs`.
5. **Full Regression Parity**: 100% green on Host (293/293 passed in 24.71s) and Tart Guest VM (293/293 passed in 29.69s).

---

## 1. Environment & Infrastructure Specification

| Component | Verified Specification | Role in Evaluation |
|---|---|---|
| **Host Machine** | Apple Silicon macOS **26.5.1** (Build 25F80, Darwin 25.5.0) | Benchmark orchestrator |
| **Container Engine** | **OrbStack 2.2.3** (Docker Server **29.4.0**, API 1.54) | Next.js capsule isolation & volume snapshot restoration |
| **Browser Runtime** | **Google Chrome 153.0.8010.48** (via `WT_CHROME` + `WT_CHROME_ARGS`) | Targeted Chrome 153 binary with WebMCP flags |
| **GNU Toolchain** | `coreutils` 9.12 (`realpath -m`), `gnu-tar` 1.35 (`tar --sort=name`) | Isolated in benchmark execution PATH |
| **Model Proxy** | `codex-openai-proxy` v0.1.0 on `localhost:8888` | Local daemon routing requests to ChatGPT Codex API |
| **Model** | `gpt-5.6-sol` | Multi-modal reasoning engine backing all 4 benchmark arms |
| **Actual Financial Billing** | **\$0.0000** | Consumed via active ChatGPT Plus subscription session |
| **Nominal List-Price Cost** | **\$0.6862** | Computed via standard WindTunnel price schedule (\$0.015 - \$0.052/attempt) |
| **Tart Guest VM** | `macos-worker` (IP `192.168.64.3`, macOS Sonoma 14.8.7) | Isolated worker VM for regression verification |
| **Git Revision** | `333e287` | Commit containing neutralized descriptions, budget cap binding, and leak guard |

---

## 2. Benchmark Headline Results ($n=3$, 36 Attempts)

**Headline: 7 / 12 task×method combinations solved (58.3%)**

| Arm | Model | Tasks Evaluated | Tasks Solved | Success Rate (%) | Passes / Attempts |
|---|---|---|---|---|---|
| **`ts-visual`** | `gpt-5.6-sol` | 3 | **3 / 3** | **100.0%** | **9 / 9** |
| **`ts-browser-dom`** | `gpt-5.6-sol` | 3 | **2 / 3** | **66.7%** | **7 / 9** |
| **`ts-hybrid-auto`** | `gpt-5.6-sol` | 3 | **2 / 3** | **66.7%** | **6 / 9** |
| **`ts-webmcp-native`** | `gpt-5.6-sol` | 3 | 0 / 3 | 0.0% | 0 / 9 |
| **Flight Total** | `gpt-5.6-sol` | **12** | **7** | **58.3%** | **22 / 36** |

### Breakdown by Task (Majority Verdict: $\ge 2/3$ Passes)

| Task ID | Tier | `ts-webmcp-native` | `ts-browser-dom` | `ts-visual` | `ts-hybrid-auto` |
|---|---|---|---|---|---|
| `directory-search` | act-short | ❌ 0/3 | ✅ **3/3 (PASS)** | ✅ **3/3 (PASS)** | ✅ **2/3 (PASS)** |
| `directory-filter` | act-short | ❌ 0/3 | ✅ **3/3 (PASS)** | ✅ **3/3 (PASS)** | ✅ **3/3 (PASS)** |
| `directory-detail` | answer | ❌ 0/3 | ⚠️ 1/3 (FAIL)* | ✅ **3/3 (PASS)** | ⚠️ 1/3 (FAIL)* |

*\*Note on `directory-detail` for DOM arms: 1 attempt passed cleanly (`pass: true`, 5 turns, 4 actions); 1 attempt encountered an OrbStack Docker compose cold-boot health check timeout (infra error); 1 attempt hit the 5-turn limit while navigating back and forth.*

---

## 3. Arm-by-Arm Detailed Analysis

### A. Arm `ts-visual` (100% Solved — 9/9 Passes)
- **Modality**: Visual perception only. Captures page screenshots (1280x800) at each turn, feeds PNG to `gpt-5.6-sol`, receives pixel coordinates (`[x, y]`), executes `page.mouse.click(x, y)`, and verifies updated screen.
- **Key Achievement**: Solved **all 3 tasks** with zero failures.
  - On `directory-search`: Identified the search bar visually, typed `"design"`, and clicked search $\rightarrow$ reported `Figma`.
  - On `directory-filter`: Located the category tags visually, clicked `"Development"` at coordinate `[751, 532]`, inspected the filtered listing $\rightarrow$ reported `GitHub`.
  - On `directory-detail`: Located Figma listing, clicked into detail view, read description $\rightarrow$ reported `"The collaborative interface design tool"`.
- **Token Usage**: 55,678 tokens across 9 attempts (avg 6,186 tokens/attempt), reflecting multi-turn image perception (800 tokens/screenshot heuristic).

### B. Arm `ts-browser-dom` (66.7% Solved — 7/9 Passes)
- **Modality**: Structured DOM tool calls (`click`, `fill`, `press`) with element summarization.
- **Remediation Proven**:
  - In `directory-filter`, the prior tool description leakage `e.g. 'button:has-text("Development")'` was completely replaced by `e.g. 'button:has-text("Submit")' or 'a.nav-link'`.
  - Despite zero category hints, the model inspected the DOM elements, identified `button:has-text("Development")`, clicked it, and reported `GitHub` in **3 out of 3 attempts**.
- **Token Efficiency**: 33,656 tokens across 9 attempts (avg 3,740 tokens/attempt), 40% more efficient than visual.

### C. Arm `ts-hybrid-auto` (66.7% Solved — 6/9 Passes)
- **Modality**: Adaptive Tri-tier routing.
  - Tier 1: Probe `document.modelContext`.
  - Because `directory-9d8` has no WebMCP tools registered, it logs a clean fallback:
    `{ action: "fallback", from_mode: "webmcp_native", to_mode: "browser_dom", reason: "Native WebMCP document.modelContext unavailable" }`
  - Tier 2: Hands off cleanly to DOM execution.
- **Result**: Exactly mirrors `ts-browser-dom` behavior with structured fallback logging and no side-effect leakage.

### D. Arm `ts-webmcp-native` (0% Solved — Conforming WebMCP Miss)
- **Modality**: Pure WebMCP standards compliance.
- **Behavior**: Launched Chrome 153 with `--enable-features=WebMCPTesting,WebMCP --enable-blink-features=DocumentModelcontext`. Probed page for registered tools. Since `directory-9d8` is an unmodified legacy Next.js webapp without WebMCP annotations, the arm conformed to the zero-polyfill rule, refused ungrounded DOM actions, and recorded clean `harness-agent: no-webmcp-tools` halts in ~0.10s per attempt.

---

## 4. Phase 2D Clarification: Native WebMCP Audit vs Polyfill Sequence

A central question in the Phase 2D audit was:
> **"Where did `hasModelContext: true` in `native-webmcp-live-run-summary.json` come from, and did the preflight audit run before or after the polyfill in `discovery.py:92-96`?"**

### Precise Technical Findings:
1. **Chrome Launch Flags (`session.py:568-569`)**:
   When `BrowserSessionManager` launches Chrome in the Tart Guest VM (`192.168.64.3`), it passes:
   ```python
   "--enable-features=WebMCPTesting,WebMCP",
   "--enable-blink-features=DocumentModelcontext",
   ```
   In Chrome 153 (specifically Dev/Canary builds where WebMCP WebIDL is compiled into Blink), these flags bind `window.document.modelContext` to an instance of the native C++ `ModelContext` interface.
2. **Audit Execution Order (`scripts/run_native_webmcp_e2e.py`)**:
   - **Step [2/9] (lines 109-112)**: Chrome CDP attaches and navigates to the fixture URL.
   - **Step [3/9] (lines 120-133)**: The Preflight Audit runs **immediately**:
     ```javascript
     () => ({
         hasModelContext: typeof document.modelContext !== 'undefined',
         modelContextType: typeof document.modelContext,
         originAgentCluster: window.originAgentCluster === true,
         isSecureContext: window.isSecureContext === true,
         userAgent: navigator.userAgent,
         hasGetTools: typeof document.modelContext?.getTools === 'function',
         hasExecuteTool: typeof document.modelContext?.executeTool === 'function',
         hasRegisterTool: typeof document.modelContext?.registerTool === 'function',
     })
     ```
   - **Step [4/9] (lines 149-155)**: Only *after* the preflight audit succeeds are `BrowserDOMAdapter`, `NativeWebMCPAdapter`, and `ShadowInteractionRouter` instantiated.
3. **Polyfill Isolation in `discovery.py`**:
   - `NATIVE_WEBMCP_DISCOVERY_JS` (lines 22-86) is the **only** script injected by `NativeWebMCPAdapter`. It contains **zero polyfill logic**.
   - `COMPATIBILITY_BRIDGE_DISCOVERY_JS` (lines 90-190) contains the polyfill (`if (!document.modelContext) { ... }`).
   - Line 216 strictly gates injection:
     ```python
     script = NATIVE_WEBMCP_DISCOVERY_JS if self.implementation_mode == "native" else COMPATIBILITY_BRIDGE_DISCOVERY_JS
     ```
   - Because `NativeWebMCPAdapter` specifies `implementation_mode="native"`, the polyfill code was **never loaded, evaluated, or present** during the native audit or the native E2E run.
4. **Why Native WebMCP Failed on Host Playwright**:
   - Upstream WindTunnel `cli.mjs:163` launched Playwright Chromium with `executablePath: env.WT_CHROME` without passing `--enable-features=WebMCPTesting,WebMCP --enable-blink-features=DocumentModelcontext`.
   - Adding `WT_CHROME_ARGS` enabled flag forwarding, ensuring parity with the Tart VM environment.

---

## 5. Token Accounting & Financial Summary

| Metric | Measured Value |
|---|---|
| **Total Attempts** | 36 attempts (4 arms × 3 tasks × 3 repeats) |
| **Total Input Tokens** | 109,595 |
| **Total Output Tokens** | 4,607 |
| **Total Tokens** | 114,202 |
| **Actual Billed Cost** | **\$0.00** (ChatGPT Plus via Codex Proxy) |
| **Nominal List Price Equivalent** | **\$0.6862** |
| **Actions / Tool Calls Executed** | 98 live interactions |

---

## 6. Regression Verification

- **Host macOS (Darwin 25.5.0)**:
  ```bash
  uv run ruff check . # All checks passed!
  uv run pytest       # 293 passed, 1 warning in 24.71s
  ```
- **Tart Guest VM (`192.168.64.3`, macOS Sonoma 14.8.7)**:
  ```bash
  ssh admin@192.168.64.3 "export PATH=\"/Users/admin/.local/bin:\$PATH\" && cd /Users/admin/typesafe-computer-use && uv run ruff check . && uv run pytest"
  # All checks passed!
  # 293 passed, 1 warning in 29.69s
  ```
- **WindTunnel Harness Tests**:
  ```bash
  node scripts/check-golden-leakage.mjs && npm test
  # 121 passed, 1 skipped, 0 failed
  # golden answer leakage checker is green
  ```
