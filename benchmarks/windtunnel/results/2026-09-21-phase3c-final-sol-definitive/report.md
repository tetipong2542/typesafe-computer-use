# WindTunnel run — 2026-09-21-phase3c-final-sol-definitive

**Date:** 2026-09-21 · **Preset:** smoke · **Sites:** directory-9d8
**Repeats per task:** 3 · **Approx. cost:** $0.6630

Reproduce:

```bash
npm run bench -- --preset smoke --sites directory-9d8 --arms ts-webmcp-native,ts-browser-dom,ts-visual,ts-hybrid-auto
```

Task set: `development tasks` · Harness commit: `ed958207583323d964a9f02cfdbb1a414ceeb915-dirty` · N: `3`

## Headline — task×method combinations solved (out of 12)

**11/12 task×method combinations solved (91.7%).**

| Method | Model | Solved | % |
|---|---|---|---|
| ts-webmcp-native | gpt-5.6-sol | 3/3 | 100.0% |
| ts-browser-dom | gpt-5.6-sol | 2/3 | 66.7% |
| ts-visual | gpt-5.6-sol | 3/3 | 100.0% |
| ts-hybrid-auto | gpt-5.6-sol | 3/3 | 100.0% |

**One-line takeaway:** ts-webmcp-native, ts-browser-dom, ts-visual, ts-hybrid-auto solved 11/12 task×method combinations.

## By difficulty tier

| Tier | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| answer | 1/1 | 1/1 | 1/1 | 1/1 |
| act-short | 2/2 | 1/2 | 2/2 | 2/2 |
| act-long | 0/0 | 0/0 | 0/0 | 0/0 |
| transaction | 0/0 | 0/0 | 0/0 | 0/0 |

## By site (optional)

| Site | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| directory-9d8 | 3/3 | 2/3 | 3/3 | 3/3 |

## Robustness (optional)

Not implemented.

## Notes

- Skipped runs (missing keys, timeouts): none recorded
- Anomalies or surprises: none recorded
- Sanity checks (the two control sites behaved as expected?): n/a — control sites not in this run
