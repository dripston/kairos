# K27 · Agent activity dashboard

**Owner:** Varun · **Epic:** 06 Tracking & analytics · **MoSCoW:** Should
**Tracker "Done when":** Filter by cohort, channel and agent. Trace one recommendation to policy, send outcome and evidence.

> SIMULATED run `RUN-s42-n50000-1cf8996d`. Double-click `outputs/dashboard.html`. It works offline.

## What the dashboard shows

- **Filters:** agent (Kairos / fixed cadence), cohort (10 segments), channel (5).
- **Acceptance checks:** lift, breaches, throughput, determinism, 500 chats.
- **Agent activity:** replies per ₹ by day, totals per agent, channel mix by day with the switch marked.
- **Decision reasons and overrides:** every borrower-day decision, typed as K01 gate, override (hold, STOP,
  cancelled at dispatch) or agent choice.
- **What Kairos learned:** best channel and time per segment, day 1 vs last day.
- **Borrower drill-down:** 400 sample borrowers; every touch from both agents with score, reasons, template, outcome and trace.

`trace_example.json` traces one recommendation:
K01 gates → K21 score and reasons → channel and time → approved template → outcome → evidence in the touch log.

## Tests (`python3 reference_check.py` → 9/9)

T01 self-contained file · T02 filters cover the run · T03 activity = raw log · T04 decision reasons and overrides ·
T05 drill-down = raw log · **T06 traced touch re-approved by Ojas's K01 reference from the raw log** ·
T07 trace fields and evidence · T08 no names or mobile numbers (ready for K28) · T09 equals K26.
