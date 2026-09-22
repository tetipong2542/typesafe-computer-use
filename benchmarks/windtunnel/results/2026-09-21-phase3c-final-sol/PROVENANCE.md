# Benchmark Run Provenance: 2026-09-21-phase3c-final-sol

## Execution Envelope

- **Run ID / Label**: `2026-09-21-phase3c-final-sol`
- **Execution Timestamp**: `2026-09-21T09:52:38Z` (16:52:38+07:00)
- **Model Target**: `gpt-5.6-sol` via `codex-openai-proxy` (`http://127.0.0.1:8888/v1`)
- **Actual Financial Cost**: **\$0.00** (ChatGPT Plus subscription token session)
- **Nominal Benchmark Cost**: **\$0.5679** (standard WindTunnel pricing schedule)
- **Harness Commit Recorded**: `6bd07a5c73eebe7bae0ace9f495020859551b2df-dirty`
- **Outcome**: **20 / 36 passes (55.6% raw, 20/32 = 62.5% valid)**; **7 / 12 combinations solved (58.3%)**

---

## Provenance Audit Disclosures

### 1. Retroactive Chrome Metadata in `run.json`
- In commit `ba9dc5a`, the fields `chrome_binary` and `chrome_args` were retroactively added into `options` of `run.json` as `null`.
- **Root Cause of WebMCP 0/9 Failure**: During this run, `WT_CHROME` and `WT_CHROME_ARGS` were omitted in the subshell invocation. Playwright launched bundled headless Chromium without WebMCP feature flags, causing `document.modelContext` to remain `undefined` and deterministically triggering `native-webmcp-unsupported` on all 9 attempts of `ts-webmcp-native`.

### 2. Working Tree Dirty Suffix (`-dirty`)
- The recorded git revision carries `-dirty` because an untracked evidence backup directory (`results/2026-09-21-phase3c-webmcp-arm1-evidence/`) was present in the repository working tree at launch time.
- **Phase 3D Governance**: All subsequent benchmarks in Phase 3D must be launched exclusively from a clean working tree (`git status --porcelain` empty).
