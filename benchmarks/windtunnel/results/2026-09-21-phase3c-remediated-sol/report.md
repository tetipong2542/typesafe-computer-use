# WindTunnel run — phase3c-remediated-sol

**Date:** 2026-09-21 · **Preset:** smoke · **Sites:** directory-9d8
**Repeats per task:** 1 · **Approx. cost:** $0.0975

Reproduce:

```bash
npm run bench -- --preset smoke --sites directory-9d8 --arms ts-webmcp-native,ts-browser-dom,ts-visual,ts-hybrid-auto
```

Task set: `development tasks` · Harness commit: `cc9df78ffb9a403a6bcb33b0155a1bffc525629b` · N: `1`

## Headline — task×method combinations solved (out of 8)

**6/8 task×method combinations solved (75.0%).**

| Method | Model | Solved | % |
|---|---|---|---|
| ts-webmcp-native | gpt-5.6-sol | 0/2 | 0.0% |
| ts-browser-dom | gpt-5.6-sol | 2/2 | 100.0% |
| ts-visual | gpt-5.6-sol | 2/2 | 100.0% |
| ts-hybrid-auto | gpt-5.6-sol | 2/2 | 100.0% |

**One-line takeaway:** ts-webmcp-native, ts-browser-dom, ts-visual, ts-hybrid-auto solved 6/8 task×method combinations.

## By difficulty tier

| Tier | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| answer | 0/0 | 0/0 | 0/0 | 0/0 |
| act-short | 0/2 | 2/2 | 2/2 | 2/2 |
| act-long | 0/0 | 0/0 | 0/0 | 0/0 |
| transaction | 0/0 | 0/0 | 0/0 | 0/0 |

## By site (optional)

| Site | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| directory-9d8 | 0/2 | 2/2 | 2/2 | 2/2 |

## Robustness (optional)

Not implemented.

## Notes

- Skipped runs (missing keys, timeouts): none recorded
- Anomalies or surprises: none recorded
- Sanity checks (the two control sites behaved as expected?): n/a — control sites not in this run
