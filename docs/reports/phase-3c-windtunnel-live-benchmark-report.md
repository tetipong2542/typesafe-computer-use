# Phase 3C: Live WindTunnel Benchmark Remediated Report & Evaluation

## Executive Summary

Phase 3C executed the live benchmark of **TypeSafe Computer Use** on the **WindTunnel** benchmark suite against the `directory-9d8` capsule. All 8 sequential attempts across 4 arms (`ts-webmcp-native`, `ts-browser-dom`, `ts-visual`, `ts-hybrid-auto`) and 2 task IDs (`directory-search`, `directory-filter`) were executed against **`gpt-5.6-sol`** via `codex-openai-proxy` (port 8888) backed by user ChatGPT Plus session tokens at **$0.00 actual billing cost** (nominal list price value: **$0.0975**).

This flight remediates all validity concerns raised from the initial dry run:
1. **True Interaction Enforced**: Eliminated passive `body.innerText` prompt dumping. Every passing attempt executed genuine DOM or visual actions (`click`, `mouse_click`) before concluding.
2. **Capsule Reset Permission Resolved**: Eliminated `SQLITE_CANTOPEN: 14` by enforcing `chmod -R a+rwX` during snapshot extraction. All 8 resets succeeded cleanly (avg 7.11s).
3. **Chrome 153 Explicit Execution**: Executed Playwright under `WT_CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` (Chrome 153.0.8010.48).
4. **Dual Cost & Image Token Disclosure**: Surfaced nominal list price ($0.0975) alongside actual billing ($0.00), with explicit disclosure of the 800 tokens/screenshot heuristic.
5. **Full Regression Parity**: 100% green on Host (293/293 passed in 24.81s) and Tart Guest VM (293/293 passed in 31.56s).

---

## 1. Environment & Infrastructure Specification

| Component | Verified Specification | Role in Evaluation |
|---|---|---|
| **Host Machine** | Apple Silicon macOS **26.5.1** (Build 25F80, Darwin 25.5.0) | Host benchmark orchestrator |
| **Container Engine** | **OrbStack 2.2.3** (Docker Server **29.4.0**, API 1.54) | Next.js capsule isolation & volume snapshot restoration |
| **Browser Runtime** | **Google Chrome 153.0.8010.48** (via `WT_CHROME`) | Official Chrome 153 binary targeted for WebMCP evaluation |
| **GNU Toolchain** | `coreutils` 9.12 (`realpath -m`), `gnu-tar` 1.35 (`tar --sort=name`) | Isolated in benchmark execution PATH |
| **Model Proxy** | `codex-openai-proxy` v0.1.0 on `localhost:8888` | Authenticated to ChatGPT Codex endpoint via `~/.codex/auth.json` |
| **Model** | `gpt-5.6-sol` | Multi-modal reasoning engine backing all 4 benchmark arms |
| **Actual Financial Billing** | **$0.0000** | Consumed via active ChatGPT Plus subscription session |
| **Nominal List-Price Cost** | **$0.0975** | Calculated via standard WindTunnel price schedule |
| **Tart Guest VM** | `macos-worker` (IP `192.168.64.3`, macOS Sonoma 14.8.7) | Isolated worker VM for regression verification |
| **Git Revision** | `cc9df78ffb9a403a6bcb33b0155a1bffc525629b` | Clean working tree commit |

---

## 2. 4-Arm Comparison Matrix

| Arm | Model | Tasks Evaluated | Solved | Success Rate | Avg Wall Clock (s) | Avg Agent Time (s) | Total Tokens | Nominal Cost | Actual Spend |
|---|---|---|---|---|---|---|---|---|---|
| **`ts-browser-dom`** | `gpt-5.6-sol` | 2 | **2 / 2** | **100.0%** | 14.32s | 7.48s | 5,216 | $0.0304 | **$0.00** |
| **`ts-hybrid-auto`** | `gpt-5.6-sol` | 2 | **2 / 2** | **100.0%** | 14.88s | 7.61s | 5,205 | $0.0301 | **$0.00** |
| **`ts-visual`** | `gpt-5.6-sol` | 2 | **2 / 2** | **100.0%** | 16.38s | 9.01s | 6,421 | $0.0370 | **$0.00** |
| **`ts-webmcp-native`** | `gpt-5.6-sol` | 2 | 0 / 2 | 0.0% | 7.05s | 0.09s | 0 | $0.0000 | **$0.00** |

*Overall Benchmark Result:* **6 / 8 task×method combinations solved (75.0%)**.

---

## 3. Detailed Attempt Log & True Interaction Proof

| # | Task ID | Arm | Status | Turns | Actions | Agent Time | Tokens (In / Out) | Nominal Cost | Verified Interaction Proof |
|---|---|---|---|---|---|---|---|---|---|
| **1** | `directory-search` | `ts-webmcp-native` | **FAIL** | 1 | 1 | 0.09s | 0 / 0 | $0.0000 | Conforming halt: Chrome 153 lacks native `document.modelContext`; zero polyfills injected. |
| **2** | `directory-filter` | `ts-webmcp-native` | **FAIL** | 1 | 1 | 0.09s | 0 / 0 | $0.0000 | Conforming halt: Chrome 153 lacks native `document.modelContext`; zero polyfills injected. |
| **3** | `directory-search` | `ts-browser-dom` | **PASS** | 2 | 1 | 8.04s | 2,517 / 99 | $0.0156 | Agent clicked `button:has-text("Design")` $\rightarrow$ verified DOM results $\rightarrow$ Answer: `Figma`. |
| **4** | `directory-filter` | `ts-browser-dom` | **PASS** | 2 | 1 | 6.93s | 2,526 / 74 | $0.0149 | Agent clicked `button:has-text("Development")` $\rightarrow$ verified DOM results $\rightarrow$ Answer: `GitHub`. |
| **5** | `directory-search` | `ts-visual` | **PASS** | 2 | 3 | 11.59s | 3,108 / 128 | $0.0194 | Shot 1 (42KB) $\rightarrow$ Mouse click at `[851, 532]` $\rightarrow$ Shot 2 (47KB) $\rightarrow$ Answer: `Figma`. |
| **6** | `directory-filter` | `ts-visual` | **PASS** | 2 | 3 | 6.43s | 3,118 / 67 | $0.0176 | Shot 1 (42KB) $\rightarrow$ Mouse click at `[751, 532]` $\rightarrow$ Shot 2 (47KB) $\rightarrow$ Answer: `GitHub`. |
| **7** | `directory-search` | `ts-hybrid-auto` | **PASS** | 2 | 2 | 8.24s | 2,517 / 104 | $0.0157 | WebMCP probe miss $\rightarrow$ fallback logged $\rightarrow$ DOM click `button:has-text("Design")` $\rightarrow$ Answer: `Figma`. |
| **8** | `directory-filter` | `ts-hybrid-auto` | **PASS** | 2 | 2 | 6.98s | 2,526 / 58 | $0.0144 | WebMCP probe miss $\rightarrow$ fallback logged $\rightarrow$ DOM click `button:has-text("Development")` $\rightarrow$ Answer: `GitHub`. |

---

## 4. Empirical Technical Findings & Analysis

1. **True Interaction Verification**:
   - In contrast to earlier text-dumping artifacts, every passing attempt required multi-turn interaction (`model_turns = 2`).
   - For DOM arms, the agent inspected interactive elements and executed real Playwright locator clicks (`button:has-text("Design")` and `button:has-text("Development")`).
   - For Visual arms, the agent grounded actions directly from image perception, dispatching exact pixel coordinates (`[851, 532]` and `[751, 532]`), followed by verification of the post-click screenshot before producing the final answer.

2. **Comparative Trade-Offs (Sample $n=1$)**:
   - **DOM Efficiency**: `ts-browser-dom` consumed the fewest tokens (2,600 - 2,616 total tokens) and ran with the lowest agent latency (6.93s - 8.04s).
   - **Visual Grounding Robustness**: `ts-visual` demonstrated full end-to-end task completion without inspecting DOM code or selectors, but incurred higher token overhead (+23% tokens) due to multi-turn screenshot perception.
   - **Hybrid Routing Determinism**: `ts-hybrid-auto` seamlessly probed native WebMCP and fell back to `browser_dom`, preserving optimal token efficiency while maintaining standards compliance.

3. **WebMCP Chrome 153 Status**:
   - Playwright launched under `WT_CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` (v153.0.8010.48).
   - `ts-webmcp-native` verified that `document.modelContext` remains undefined in current standard Chrome 153 builds without experimental flags or developer origin trials.
   - The arm strictly conformed to the zero-polyfill rule, refusing ungrounded DOM actions and registering expected standard halts.

4. **Accounting Transparency & Token Disclosures**:
   - **Financial billing**: Strictly $0.00 billed to user API balances.
   - **Nominal cost**: Disclosed as $0.0975 across the 8 attempts ($0.0304 DOM, $0.0370 Visual, $0.0301 Hybrid).
   - **Image token counting heuristic**: Disclosed in telemetry as 800 tokens per 1280x800 screenshot.

---

## 5. Regression Audit Proof

- **Host Machine Tests**:
  ```bash
  uv run ruff check . # All checks passed!
  uv run pytest       # 293 passed, 1 warning in 24.81s
  ```
- **Tart Guest VM Tests (`192.168.64.3`)**:
  ```bash
  ssh admin@192.168.64.3 "export PATH=\"/Users/admin/.local/bin:\$PATH\" && cd /Users/admin/typesafe-computer-use && uv run ruff check . && uv run pytest"
  # All checks passed!
  # 293 passed, 1 warning in 31.56s
  ```
- **WindTunnel Harness Unit Tests**:
  ```bash
  node --test tests/model-driver.test.mjs tests/typesafe-arms.test.mjs
  # 29 passed, 0 failed in 70.45ms
  ```

---

## 6. Artifact & Evidence Locations

- **Run Report**: `benchmarks/windtunnel/results/2026-09-21-phase3c-remediated-sol/report.md`
- **Run Data CSV**: `benchmarks/windtunnel/results/2026-09-21-phase3c-remediated-sol/results.csv`
- **Run JSON Record**: `benchmarks/windtunnel/results/2026-09-21-phase3c-remediated-sol/run.json`
- **Tart Checkpoints Preserved**:
  - `macos-worker-phase3a-prebench-checkpoint`
  - `macos-worker-phase3c-postbench-checkpoint`
