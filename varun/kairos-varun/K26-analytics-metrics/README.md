# K26 · Analytics data and metrics

**Owner:** Varun · **Epic:** 06 Tracking & analytics · **MoSCoW:** Should
**Tracker "Done when":** Reconcile each KPI to source events. Show rate denominators and label live, replay and simulated runs.

> SIMULATED run `RUN-s42-n50000-1cf8996d`.

## What's here

- **`borrower_activity.csv.gz`:** the joined table. One row per borrower per agent: eligible, attempted, touches,
  reached, replies, spend, paid, plus loan, obligation and segment. 100,000 rows, no orphan events.
- **`kpis.csv`:** 11 KPIs per agent. Each row names its numerator, its denominator and the source event of each,
  plus run label, run ID, seed and baseline. Funnel rates chain: each denominator is the previous stage.
- **`kpis_by_segment.csv`:** the same per segment; adds up to the totals.

| KPI (Kairos) | Numerator / denominator | Value |
|---|---|---:|
| Replies per ₹ | 21,168 replies / ₹2,44,229 spend | 0.0867 |
| Response rate | 16,943 responders / 49,069 reached | 34.5% |
| Payment after reply | 8,653 paid / 16,943 responders | 51.1% |

## Tests (`python3 reference_check.py` → 9/9)

T01 joins · T02 KPI fields · **T03 all 22 KPIs rebuilt from raw events, 0 differences** · T04 denominators chain ·
T05 unique counting · T06 spend reconciles four ways · T07 run labels (invalid label rejected) · T08 segments add up · T09 equals K15.
