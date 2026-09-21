# WindTunnel run — phase3c-n3-verified-sol

**Date:** 2026-09-21 · **Preset:** smoke · **Sites:** directory-9d8
**Repeats per task:** 3 · **Approx. cost:** $0.6862

Reproduce:

```bash
npm run bench -- --preset smoke --sites directory-9d8 --arms ts-webmcp-native,ts-browser-dom,ts-visual,ts-hybrid-auto
```

Task set: `development tasks` · Harness commit: `333e287f34c1195a4537e83b8367006314ff9a2c` · N: `3`

## Headline — task×method combinations solved (out of 12)

**7/12 task×method combinations solved (58.3%).**

| Method | Model | Solved | % |
|---|---|---|---|
| ts-webmcp-native | gpt-5.6-sol | 0/3 | 0.0% |
| ts-browser-dom | gpt-5.6-sol | 2/3 | 66.7% |
| ts-visual | gpt-5.6-sol | 3/3 | 100.0% |
| ts-hybrid-auto | gpt-5.6-sol | 2/3 | 66.7% |

**One-line takeaway:** ts-webmcp-native, ts-browser-dom, ts-visual, ts-hybrid-auto solved 7/12 task×method combinations.

## By difficulty tier

| Tier | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| answer | 0/1 | 0/1 | 1/1 | 0/1 |
| act-short | 0/2 | 2/2 | 2/2 | 2/2 |
| act-long | 0/0 | 0/0 | 0/0 | 0/0 |
| transaction | 0/0 | 0/0 | 0/0 | 0/0 |

## By site (optional)

| Site | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| directory-9d8 | 0/3 | 2/3 | 3/3 | 2/3 |

## Robustness (optional)

Not implemented.

## Notes

- Skipped runs (missing keys, timeouts): none recorded
- Anomalies or surprises: none recorded
- Sanity checks (the two control sites behaved as expected?): n/a — control sites not in this run
