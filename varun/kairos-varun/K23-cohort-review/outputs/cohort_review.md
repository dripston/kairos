# K23 · Cohort close and policy review (RUN-s42-n50000-1cf8996d)

> **SIMULATED** dev run. Cohort: 50,000 borrowers, 2026-11-23 to 2026-12-20. Every number below is computed from the run's touch and payment logs.

## 1. The mid-campaign switch

Kairos's channel mix changed most on **day 15 (2026-12-08)** for Salaried borrowers (sms 31% -> 53%). For validation only: the simulator's hidden drift was on day 12 (gap +3 days). The review below compares the whole period before the switch with the whole period after it.

## 2. Both policies over the cohort

| Policy | Unique reach | Meaningful replies | Verified payments after a reply | Spend (₹) |
|---|---:|---:|---:|---:|
| fixed_cadence | 49,096 | 14,908 | 6,190 | 323,497 |
| kairos | 49,069 | 21,168 | 8,653 | 244,229 |

## 3. Channels before vs after the switch (Kairos)

| Employment | Channel | Reply rate before | after | Replies per ₹ before | after | Payments after reply before | after |
|---|---|---:|---:|---:|---:|---:|---:|
| Salaried | sms | 1.72% | 2.08% | 0.086 | 0.104 | 982 | 931 |
| Salaried | whatsapp | 3.74% | 2.62% | 0.094 | 0.065 | 2,347 | 459 |
| Salaried | email | 0.66% | 0.55% | 0.131 | 0.110 | 400 | 117 |
| Salaried | ivr | 3.58% | 1.95% | 0.030 | 0.016 | 111 | 20 |
| Salaried | voicebot | 1.89% | 1.15% | 0.008 | 0.005 | 3 | 0 |
| Self-employed | sms | 1.45% | 1.32% | 0.073 | 0.066 | 622 | 231 |
| Self-employed | whatsapp | 4.10% | 4.38% | 0.102 | 0.109 | 1,259 | 781 |
| Self-employed | email | 0.65% | 0.47% | 0.129 | 0.095 | 256 | 56 |
| Self-employed | ivr | 3.20% | 1.58% | 0.027 | 0.013 | 68 | 9 |
| Self-employed | voicebot | 0.60% | 1.56% | 0.003 | 0.006 | 1 | 0 |

## 4. Send times after the switch (Kairos, replies per ₹)

| Employment | 08:00 | 10:00 | 12:00 | 14:00 | 16:00 | 18:00 |
|---|---:|---:|---:|---:|---:|---:|
| Salaried | 0.037 | 0.026 | 0.101 | 0.039 | 0.039 | 0.055 |
| Self-employed | 0.111 | 0.079 | 0.047 | 0.043 | 0.053 | 0.041 |

## 5. Proposed changes (none applied)

| ID | Change | Needs Ojas? | Status |
|---|---|---|---|
| PROP-01 | Lower the starting weight of whatsapp for Salaried borrowers in the next cohort | No | PROPOSED |
| PROP-02 | Lower the starting weight of ivr for Salaried borrowers in the next cohort | No | PROPOSED |
| PROP-03 | Lower the starting weight of ivr for Self-employed borrowers in the next cohort | No | PROPOSED |
| PROP-04 | Start Salaried borrowers at 12:00 in the next cohort (best replies per ₹ after the switch) | No | PROPOSED |
| PROP-05 | Start Self-employed borrowers at 08:00 in the next cohort (best replies per ₹ after the switch) | No | PROPOSED |
| PROP-06 | Allow IVR only for B2+ bands (today it is allowed in every band) | **Yes** | PENDING_POLICY_OWNER_APPROVAL |
| PROP-07 | Consider a cross-channel daily ceiling (K01 sets none today) | **Yes** | PENDING_POLICY_OWNER_APPROVAL |
| PROP-08 | Register an approved Hindi voicebot template (Hindi borrowers' voicebot touches WAIT today) | No | PROPOSED |

Evidence for each proposal is in `cohort_review.json` → `proposals[].evidence`.

**Governance:** Changes to caps, consent, contact window, hardship or PTP holds require Ojas (Product) approval.

## 6. Rollback

Learned beliefs are applied only as next-cohort starting weights. To roll back, start the next cohort from priors_pre_cohort.json (history-only prior). K01/K21 policy files are never edited by this review.
Snapshots: `priors_pre_cohort.json` (d8fcf3ecadf2) and `priors_post_cohort.json` (e4f8179fd6f9).
