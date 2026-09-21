# WindTunnel run — phase3c-live-sol

**Date:** 2026-09-21 · **Preset:** smoke · **Sites:** directory-9d8
**Repeats per task:** 1 · **Approx. cost:** $0.0000

Reproduce:

```bash
npm run bench -- --preset smoke --sites directory-9d8 --arms ts-webmcp-native,ts-browser-dom,ts-visual,ts-hybrid-auto
```

Task set: `development tasks` · Harness commit: `6bdd1a4a6e5bd8c8886a57f26c750cf5c8c0a17f-dirty` · N: `1`

## Headline — task×method combinations solved (out of 8)

**5/8 task×method combinations solved (62.5%).**

| Method | Model | Solved | % |
|---|---|---|---|
| ts-webmcp-native | gpt-5.6-sol | 0/2 | 0.0% |
| ts-browser-dom | gpt-5.6-sol | 1/2 | 50.0% |
| ts-visual | gpt-5.6-sol | 2/2 | 100.0% |
| ts-hybrid-auto | gpt-5.6-sol | 2/2 | 100.0% |

**One-line takeaway:** ts-webmcp-native, ts-browser-dom, ts-visual, ts-hybrid-auto solved 5/8 task×method combinations.

## By difficulty tier

| Tier | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| answer | 0/0 | 0/0 | 0/0 | 0/0 |
| act-short | 0/2 | 1/2 | 2/2 | 2/2 |
| act-long | 0/0 | 0/0 | 0/0 | 0/0 |
| transaction | 0/0 | 0/0 | 0/0 | 0/0 |

## By site (optional)

| Site | ts-webmcp-native | ts-browser-dom | ts-visual | ts-hybrid-auto |
|---|---|---|---|---|
| directory-9d8 | 0/2 | 1/2 | 2/2 | 2/2 |

## Robustness (optional)

Not implemented.

## Notes

- Skipped runs (missing keys, timeouts): none recorded
- Anomalies or surprises: none recorded
- Sanity checks (the two control sites behaved as expected?): n/a — control sites not in this run
