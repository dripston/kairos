# K23 · Cohort close + policy review

**Owner:** Varun · **Epic:** 05 Measure & demo · **MoSCoW:** Must · **Policy owner:** Ojas
**Tracker "Done when":** Show cohort review after the mid-campaign switch. Changes to caps, consent and
hardship rules require policy-owner approval.

> SIMULATED run `RUN-s42-n50000-1cf8996d`. Every table is computed from the touch and payment logs.

## What the review found

- **Switch:** Kairos's channel mix changed most on **2026-12-08** (salaried: SMS 31% → 53%). It is found from
  Kairos's own behaviour, not from the simulator. The simulator's hidden drift was 3 days earlier.
- **After the switch:** Kairos 0.0849 vs fixed cadence 0.0364 replies per ₹ (+133%).
- **Both policies:** unique reach 49,069 vs 49,096 · meaningful replies 21,168 vs 14,908 ·
  verified payments after a reply 8,653 vs 6,190.
- **Channels and times:** reply rate, replies per ₹, unique reach and payments per channel and per send time,
  before vs after, in `cohort_review.md`.

## Proposals (none applied)

| ID | Change | Needs Ojas? |
|---|---|---|
| PROP-01..03 | Lower starting weights for channels whose reply rate fell (salaried WhatsApp and IVR, self-employed IVR) | No (learning) |
| PROP-04..05 | Default send times: salaried 12:00, self-employed 08:00 | No (learning) |
| PROP-06 | Allow IVR only for B2+ bands | **Yes: PENDING** |
| PROP-07 | Consider a cross-channel daily ceiling (STOP rate rises with contact pressure) | **Yes: PENDING** |
| PROP-08 | Register a Hindi voicebot template | No (Yuval, K22) |

**Rollback:** `priors_pre_cohort.json` restores the exact starting beliefs (tested). Policy files are never edited (tested by hash).

## Tests (`python3 reference_check.py` → 9/9)

T01 switch re-detected from raw logs · T02 channel table · T03 send-time table · T04 reconciles with K15 ·
T05 proposals carry evidence · T06 governance · T07 rollback · T08 policy files unchanged · T09 post-switch outcome.

## Outputs

`cohort_review.md` · `cohort_review.json` · `priors_pre_cohort.json` · `priors_post_cohort.json` · `run_manifest.json`
