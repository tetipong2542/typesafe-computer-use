# Benchmark Run Provenance: 2026-09-21-phase3c-final-sol-definitive

## Execution Envelope

- **Run ID / Label**: `2026-09-21-phase3c-final-sol-definitive`
- **Execution Timestamp**: `2026-09-21T10:16:08Z` (17:16:08+07:00)
- **Model Target**: `gpt-5.6-sol` via `codex-openai-proxy` (`http://127.0.0.1:8888/v1`)
- **Actual Financial Cost**: **\$0.00** (ChatGPT Plus subscription token session)
- **Nominal Benchmark Cost**: **\$0.6630** (standard WindTunnel pricing schedule)
- **Harness Commit Recorded**: `ed958207583323d964a9f02cfdbb1a414ceeb915-dirty`
- **Outcome**: **33 / 36 passes (91.7% raw, 33/34 = 97.1% valid)**; **11 / 12 combinations solved (91.7%)**

---

## Provenance Audit Disclosures

### 1. Retroactive Chrome Metadata in `run.json`
- In commit `ba9dc5a`, the fields `chrome_binary` and `chrome_args` were retroactively added into `options` of `run.json`.
- **Reason**: The benchmark runner code in commit `ed95820` did not yet serialize `env.WT_CHROME` and `env.WT_CHROME_ARGS` into the output JSON.
- **Verification**: The environment variables active during execution were confirmed as:
  - `WT_CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` (Google Chrome 153.0.8010.48)
  - `WT_CHROME_ARGS="--enable-features=WebMCPTesting,WebMCP --enable-blink-features=DocumentModelcontext"`
- These flags enabled Blink C++ native WebMCP discovery (`typeof document.modelContext === "object"`), yielding 9/9 WebMCP passes with telemetry recorded in `run.json`.

### 2. Working Tree Dirty Suffix (`-dirty`)
- The recorded git revision carries `-dirty` because untracked prior benchmark output directories and evidence backups in `benchmarks/windtunnel/results/` were present in the working tree when `harness/cli.mjs` was invoked.
- **Phase 3D Governance**: All subsequent benchmarks in Phase 3D must be launched exclusively from a clean working tree (`git status --porcelain` empty).
