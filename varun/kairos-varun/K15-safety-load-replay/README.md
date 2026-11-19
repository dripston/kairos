# K15 · Safety, load and story replay

**Owner:** Varun · **Epic:** 05 Measure & demo · **MoSCoW:** Must · **Edge cases:** E01 E04 E06 E17 E18
**Tracker "Done when":** Report 50K planning throughput and 500 concurrent chats. Compare unique reach,
replies per rupee and verified payments across both policies.

> SIMULATED run `RUN-s42-n50000-1cf8996d` · seed 42 · 50,000 borrowers · 28 days · equal ₹4,00,000 pool and K01 caps for both policies.

## Results (all computed from the raw touch and payment logs)

| Measure | Fixed cadence | Kairos |
|---|---:|---:|
| Eligible → attempted → delivered → replied → paid after reply | 49,226 → 49,154 → 49,096 → 13,059 → 6,190 | 49,226 → 49,127 → 49,069 → 16,943 → 8,653 |
| Replies per ₹ | 0.0461 | **0.0867 (+88.1%)** |
| Unique reach | 49,096 | 49,069 |
| Verified payments after a reply | 6,190 | **8,653** |
| Cap breaches (Ojas's K01 reference on every send) | 0 of 1,360,836 | 0 of 1,034,425 |
| Slowest daily plan for 50,000 borrowers | 0.32 s | 0.32 s |

**500 concurrent chats:** 500 conversations at once through Ojas's K10 gate, 2,040 turns, first reply
within the 30 s SLA for 100%, **0 leaks** (impostors, unregistered numbers and injections got no account data).

## Tests (`python3 reference_check.py` → 15/15)

T01 dataset traceability · T02 50K throughput · T03 equal conditions · T04 funnel from raw logs ·
T05 policy comparison · T06 ≥20% lift · T07 every send audited · **T08 auditor catches 5 injected breaches** ·
T09 500 chats · **T10 checker catches a leaking agent** · T11 E01 bands · T12 E04 · T13 E06 · T14 E17 determinism · T15 E18 labels.
T08 and T10 are negative tests: they prove the safety checks can fail.

## Outputs

`benchmark.md` · `summary.json` · `funnel.csv` · `segment_daily.csv` · `daily_totals.csv` · `decisions_daily.csv` ·
`timings.csv` · `channel_mix_*.csv` · `learning_daily.csv` · `audit_*.json` · `chat_load_report.json` · `run_manifest.json`

## Not covered here

Live CM-03 service load (needs Rehan/Yuval's app: `chat_load.py --target <url>`), screenshot OCR / D7 matching (K12),
provider callback duplicates (E04, needs K07/K08), official simulator and hidden seed (K02).
