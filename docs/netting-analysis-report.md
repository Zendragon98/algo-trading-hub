# Netting analysis report

## Summary (all runs with fills.jsonl)

| Metric | Value |
|--------|-------|
| Run folders scanned | 77 |
| Runs with fills | 51 |
| Runs with netting log activity | 2 |
| Total fills | 21,495 |
| Net venue notional (USD) | $3,150,552.99 |
| Fees paid (USDT) | $1,058.71 |
| Realized PnL (fills, sum) | $-1,173.61 |
| Implied fee rate (bps on notional) | 3.36 |
| MM fills / notional | 364 / $5,567.73 |
| Alpha (VWAP) fills / notional | 21,131 / $3,144,985.27 |
| Netted parents (`__netted__`) | 1,681 |
| Single-strategy parents | 104 |
| Net submit log events | 8,738 |
| Multi-strategy net events | 0 |
| Full cancellations (net zero) | 0 |

## Estimated savings (conservative)

| Estimate | Value |
|----------|-------|
| Avoided orders (net-zero events) | 0 |
| Avoided notional (proxy: net-zero × avg fill) | $0.00 |
| Estimated fee savings (@ 4.0 bps) | $0.00 |

**Note:** `fills.jsonl` records post-net venue execution. Gross per-strategy quantities are not persisted; multi-strategy gross-vs-net dollar savings require future `contributions` logging or log correlation.

## Top runs by fill count

| Run | Fills | Notional | Fees | Net-zero |
| 2026-05-16T14-13-50Z | 3,760 | $469,718 | $161.03 | 0 |
| 2026-05-23T16-21-06Z | 3,468 | $346,307 | $119.55 | 0 |
| 2026-05-29T13-34-44Z | 2,587 | $259,874 | $93.27 | 0 |
| 2026-05-16T04-33-11Z | 2,572 | $452,894 | $155.03 | 0 |
| 2026-05-09T08-50-38Z | 2,295 | $240,133 | $71.16 | 0 |
| 2026-05-09T08-39-18Z | 1,049 | $103,253 | $35.10 | 0 |
| 2026-05-17T07-48-42Z | 627 | $211,309 | $77.31 | 0 |
| 2026-05-16T07-25-10Z | 578 | $176,559 | $58.60 | 0 |
| 2026-05-23T15-31-08Z | 525 | $127,293 | $37.92 | 0 |
| 2026-05-17T07-06-57Z | 462 | $65,846 | $19.88 | 0 |

## Runs with STRATEGY=all netting activity

- **2026-05-23T16-21-06Z**: multi-net=0, net-zero=0, netted_parents=680
- **2026-05-29T13-34-44Z**: multi-net=0, net-zero=0, netted_parents=1001
