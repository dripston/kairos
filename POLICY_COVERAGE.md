# Policy coverage: Ojas's policy pack vs the Kairos code

Ojas (Product) owns policy; his pack (`ojas-commits/kairos-policy`, commit `e4d4d2a`) is the source of truth.
It is imported byte-for-byte into [`policy/`](policy/README.md). This page shows, case by case, whether
**our** code does what each of his `test_cases.csv` rows says. His `reference_check.py` scripts test his
own reference logic. They do not test our code, so they are not used here.

## Summary

- **K01 outreach gate: 38/38 pass.** v1.1.1 changes one rule, the campaign pool (see the K01 diff below).
  Our ledger already counts one case per loan. `Ledger.unenroll` now lets a loan leave the budgeted set,
  as K24 requires.
- **K13 exceptions: 25/25 pass.** His K13 is unchanged.
- **K10 "Kaira" inbound WhatsApp: 33/33 pass**, but only with `identity_mode="k10"`. The CM-03 default
  is still OTP, because that is what the CM-03 brief asks for. This needs Product's call (OPEN_DECISION K10-1).
  Fixed in both modes:
  - before verification, hardship is acknowledged and held and a callback is created;
  - abuse, legal threats and fraud claims end the chat with a ticket;
  - a foreclosure quote is routed to a person and never given in chat;
  - with 2 broken promises, a "needs time" reply pushes payment now.
- **K21 propensity / intensity: 20/20 pass**, using the new `kairos_backend/scorecard.py`. It is not yet
  wired into the Supervisor (OPEN_DECISION K21-1).
- **K24 portfolio intake: 32/32 pass**, using the new `spine/intake.py`. His script prints 32 checks;
  its summary line says 31.
- **K15 safety / load / replay: 11 pass, 1 not implemented.** The missing case is K15-T11, an explicit
  SMS-to-WhatsApp switch rule. 4 cases come from full-scale script runs (marked "report").
- **T01 outbound templates:** all 300 are imported into our K22 format (`t01_catalog.json`), and
  **our checker flags all 300.** None of the flags are for threats, legal-action or third-party
  wording. Most come from format differences or real rule conflicts between T01 and our K22 checklist
  (OPEN_DECISION T01-1 to T01-4, plus one defect inside T01). His text was not edited, and the live
  catalog (`catalog.json`) is unchanged.

Evidence labels: K15 numbers come from the DEV simulator and seeded fixtures, so they are **SIMULATED**.

## Import notes

- **Copied:** K01, K10, K13, K15, K21, K24, T01 and `docs/`. The three PDFs are 64 KB, 73 KB and
  91 KB, so all were imported. Files are kept byte-for-byte, including CRLF line endings. **None of
  Ojas's files were edited.**
- **Top-level README:** his top-level `README.md` is kept as `policy/OJAS_PACK_README.md`, so that
  `policy/README.md` can stay ours.
- **K01 README header:** the README still says "v1.1.0" while `policy.json` says 1.1.1. This is left
  as-is for Ojas to fix.
- **K15 README path:** the run path in the K15 README points at a personal machine. His
  `reference_check.py` imports `replay_engine` from its own folder, so it fails under `python -I`.
- **Kairos-side packs kept:** K06 HITL, K17 voice dispositions, K22 template catalog and K27 auto-churn.

### K01 v1.1.0 → v1.1.1 (semantic diff)

| Field | v1.1.0 (ours) | v1.1.1 (Ojas) | Effect on our code |
|---|---|---|---|
| `policy_version` | 1.1.0 | 1.1.1 | Every decision and event now cites 1.1.1. Tests that pinned "1.1.0" now read the version from `policy.json`. |
| `budget.campaign_pool_inr` | `budget_per_case_inr * allocated_cases` | `budget_per_case_inr * count(budgeted loans)`: a case is one loan account, and the budgeted set is explicitly selected at intake (K24 `budget_scope`) | `Ledger` already counted one case per enrolled loan. Added `Ledger.unenroll` so a loan removed by the lender leaves the set: the pool recomputes, committed spend stays, and queued touches for that loan are canceled. `spine/intake.py` blocks arming until the set is selected. |
| `test_cases.csv` | 38 rows | the same 38 rows, CRLF line endings | none |
| README | v1.1.0 header | unchanged (still says v1.1.0) | none |

**K13:** identical to ours (1.0.0); no semantic change.

### reference_check.py results (his scripts on his reference logic, Python 3.9)

| Module | Result |
|---|---|
| K01 | 38/38 passed (policy 1.1.1) |
| K10 | 33/33 passed (conversation 1.0.0, 32 scripts x 3 languages) |
| K13 | 25/25 passed (exceptions 1.0.0) |
| K15 | 12/12 passed, 50,000 decisions in 11.3 s. Fails under `python -I`, because it imports `replay_engine` from its own folder. Rewrites `benchmark_report.json`, so it was run on a scratch copy. |
| K21 | 20/20 passed, 50,000 scores in 1.3 s |
| K24 | prints 32 PASS lines, summary says "31/31 passed" |
| T01 | PASS, 0 problems; rebuilds `templates.csv` identically |

## OPEN_DECISIONs (Product to decide; we did not guess)

| Id | Question | Option A | Option B | Now |
|---|---|---|---|---|
| **K10-1** | How does CM-03 verify identity? | **K10:** registered number + name confirmation, OTP off (`otp_step_up.enabled: false`, decided 19 Nov). | **CM-03 brief / our acceptance bar:** registered number + 6-digit OTP ("zero disclosure before verification (OTP)", OTP service among the required tools). Option C is to flip K10's `otp_step_up.enabled` to true: name, then OTP. Both are already built. | Default `identity_mode="otp"`; `identity_mode="k10"` implements K10 and passes 33/33 |
| **K10-2** | A prompt-injection attempt, in OTP mode | **K10:** ignore it, log `INJECTION_ATTEMPT`, reply `INJECTION_SAFE`, carry on (done in k10 mode). | **Current CM-03 default:** refuse, open a FRAUD_SUSPECT ticket, hand off (counts as a caught fake in the CM-03 eval). | OTP mode = B, k10 mode = A |
| **K10-3** | A PTP more than 7 days out, in OTP mode | **K10/K13:** capture it at once for supervisor review (done in k10 mode). | **Current CM-03 default:** counter once with a date inside 7 days, then capture for review. | OTP mode = B, k10 mode = A |
| **K10-4** | Reply wording outside identity, safety and exception turns | Render all 32 K10 scripts, including Hinglish (`scripts.csv`). | Keep CM-03's richer en/hi copy for dues, link, receipt and statement (multi-loan, product, outstanding). | k10 mode uses his scripts for identity, safety and exception replies and ours for service replies. Hinglish is supported only for K10 scripts. |
| **K21-1** | Which propensity drives intensity in the Supervisor/K09? | Ojas's K21 scorecard (`scorecard.py`: points, HIGH band 40–59, low-evidence cap). | Current `propensity.py` rule (probability, LOW/STANDARD/WAIT, no HIGH). Switching changes CM-02 replay results, so it needs a matched re-run. | B wired, A available and tested |
| **T01-1** | Must every outbound message carry a helpline? (240 rows) | K22 rule: `{{helpline}}` on every message, and a pay link on every reminder. | T01: a HELP/CALL reply or IVR keypad option is enough; angles D and E carry no link. | Flagged, not changed |
| **T01-2** | Pre-due and T1 reminders: "ignore if already paid"? (60 rows) | Required (K22). | Not needed (T01). | Flagged |
| **T01-3** | Credit-record and late-charge wording in B1/B2, and in voicebot reminders (16 rows) | K22 tier ladder: bureau wording only from T3; no consequence wording in voicebot reminders. | T01: factual credit-record benefit from B1 (angle F) and late charges in voicebot angle C. | Flagged |
| **T01-4** | B3/B4 content (96 rows) | Ojas adds days overdue (T3) and a final date plus next stage (T4) to B3/B4. | Relax our T3/T4 content rules for T01. | Flagged |
| **T01-5** | Promote T01 to the live catalog? | Replace `catalog.json` with T01 once T01-1 to T01-4 are settled and the format mismatches are fixed (`xx{loan_last4}`, "This call is for", `{unsubscribe_link}`, Sarvam agent refs). | Keep K22 live and use T01 as the K09 angle source later. | `t01_catalog.json` is generated but not live |
| **K24-1** | A loan accepted earlier that fails validation in a re-upload | REMOVED_BY_LENDER (built). | Keep it UNCHANGED and only report it as quarantined. | A |
| **K24-2** | Unenrolling a cured loan: collection-target count | Keep the cured count and money collected as history (built). | Remove the loan's contribution as well. | A |

Defect inside T01, for Ojas to fix in `template_source.json`: in the angle-A IVR and voicebot rows,
English names the loan's last 4 digits and Hindi drops them (`IV-B1-A-EN` vs `IV-B1-A-HI`; 20 rows).

## How to re-run

```bash
cd backend
pytest tests/policy_conformance                      # live cases vs the recorded matrix (ratchet)
python scripts/policy_coverage.py                    # rewrite the matrix below
python scripts/policy_coverage.py --k15-evidence ev.json --write-snapshot   # with full-scale K15 numbers
```

K15 "report" cases come from full-scale runs:
- `scripts/bench_ledger.py 50000`: K15-T01;
- `scripts/run_k09_replay.py --check-determinism`: K15-T02, T03 and T12;
- `scripts/run_cm03_eval.py`: K15-T04 to T06, read from `reports/cm03_eval.json`.

<!-- BEGIN GENERATED (scripts/policy_coverage.py) -->
## Totals

| Module | Cases | PASS | FAIL | NOT_IMPLEMENTED | NOT_APPLICABLE |
|---|---|---|---|---|---|
| K01-decision-policy | 38 | 38 | 0 | 0 | 0 |
| K10-inbound-conversation | 33 | 33 | 0 | 0 | 0 |
| K13-operational-exceptions | 25 | 25 | 0 | 0 | 0 |
| K15-safety-load-replay | 12 | 9 | 0 | 1 | 2 |
| K21-propensity-intensity | 20 | 20 | 0 | 0 | 0 |
| K24-portfolio-intake | 32 | 32 | 0 | 0 | 0 |
| T01-outbound-templates | 300 | 0 | 300 | 0 | 0 |
| **All** | **460** | **157** | **300** | **1** | **2** |

## Matrix (module x case)

### K01-decision-policy

PASS 38

| Case | Status | Description | Our result |
|---|---|---|---|
| K01-T01 | PASS | Normal B1 WhatsApp send | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T02 | PASS | DPD 30 is B1 (cap 3) - 3rd send allowed | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T03 | PASS | DPD 30 is B1 - 4th send blocked | gate WAIT/CHANNEL_CAP (policy 1.1.1) |
| K01-T04 | PASS | DPD 31 is B2 (cap 6) - 4th send allowed | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T05 | PASS | DPD 60 is B2 - 7th send blocked | gate WAIT/CHANNEL_CAP (policy 1.1.1) |
| K01-T06 | PASS | DPD 61 is B3 (cap 9) | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T07 | PASS | DPD 91 is B4 (cap 12) | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T08 | PASS | Missing DPD holds outreach | gate WAIT/DPD_INVALID (policy 1.1.1) |
| K01-T09 | PASS | Stale DPD (>24h) holds outreach | gate WAIT/DPD_INVALID (policy 1.1.1) |
| K01-T10 | PASS | Pre-due: 3rd contact across channels allowed | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T11 | PASS | Pre-due: 4th contact across channels blocked | gate WAIT/PRE_DUE_TOTAL_CAP (policy 1.1.1) |
| K01-T12 | PASS | Before 08:00 blocked | gate WAIT/CONTACT_WINDOW (policy 1.1.1) |
| K01-T13 | PASS | 19:00 is outside window (end exclusive) | gate WAIT/CONTACT_WINDOW (policy 1.1.1) |
| K01-T14 | PASS | 18:59 inside window | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T15 | PASS | Sunday is a quiet day | gate WAIT/QUIET_DAY (policy 1.1.1) |
| K01-T16 | PASS | National holiday is a quiet day | gate WAIT/QUIET_DAY (policy 1.1.1) |
| K01-T17 | PASS | Same channel inside 2h gap blocked | gate WAIT/SAME_CHANNEL_GAP (policy 1.1.1) |
| K01-T18 | PASS | Different channel inside 2h of last SMS allowed (gap is per channel) | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T19 | PASS | STOP on WhatsApp suppresses SMS too | gate CANCEL/STOP_OPT_OUT (policy 1.1.1) |
| K01-T20 | PASS | No consent for channel | gate WAIT/NO_CONSENT (policy 1.1.1) |
| K01-T21 | PASS | Call picked up >10s earlier today stops further outreach | gate WAIT/STOP_FOR_DAY (policy 1.1.1) |
| K01-T22 | PASS | PTP date in future holds all activity | gate WAIT/PTP_HOLD (policy 1.1.1) |
| K01-T23 | PASS | Obligation cleared by verified payment cancels queued touch | gate CANCEL/OBLIGATION_CLEARED (policy 1.1.1) |
| K01-T24 | PASS | Hardship pause active | gate WAIT/HARDSHIP_HOLD (policy 1.1.1) |
| K01-T25 | PASS | Hardship date beyond 4 days goes to supervisor | gate HANDOFF/HARDSHIP_HOLD (policy 1.1.1) |
| K01-T26 | PASS | Official cap tighter than policy wins | gate WAIT/CHANNEL_CAP (policy 1.1.1) |
| K01-T27 | PASS | No approved template means WAIT | gate WAIT/NO_APPROVED_TEMPLATE (policy 1.1.1) |
| K01-T28 | PASS | Budget insufficient for this send | gate WAIT/BUDGET_EXHAUSTED (policy 1.1.1) |
| K01-T29 | PASS | Collection target achieved stops campaign | gate WAIT/CAMPAIGN_TARGET_MET (policy 1.1.1) |
| K01-T30 | PASS | STOP outranks every other rule (precedence) | gate CANCEL/STOP_OPT_OUT (policy 1.1.1) |
| K01-T31 | PASS | DPD -6 is first pre-due day (in scope) | gate SEND/ELIGIBLE (policy 1.1.1) |
| K01-T32 | PASS | DPD -7 is before pre-due start (out of scope) | gate WAIT/NOT_IN_SCOPE (policy 1.1.1) |
| K01-T33 | PASS | Lender B1 per-channel cap 2 tighter than policy 3 | gate WAIT/CHANNEL_CAP (policy 1.1.1) |
| K01-T34 | PASS | Lender IVR override 1/day | gate WAIT/CHANNEL_CAP (policy 1.1.1) |
| K01-T35 | PASS | Lender all-channel daily cap (B2=6) reached | gate WAIT/ALL_CHANNEL_DAY_CAP (policy 1.1.1) |
| K01-T36 | PASS | Lender weekly per-channel cap (B1=8) reached | gate WAIT/WEEKLY_CAP (policy 1.1.1) |
| K01-T37 | PASS | Lender 180-min gap tighter than policy 120 | gate WAIT/SAME_CHANNEL_GAP (policy 1.1.1) |
| K01-T38 | PASS | Lender caps present but not reached - send | gate SEND/ELIGIBLE (policy 1.1.1) |

### K10-inbound-conversation

PASS 33

| Case | Status | Description | Our result |
|---|---|---|---|
| K10-T01 | PASS | New inbound message gets instant safe ACK | replies=['ACK', 'ASK_NAME'] shared=N verified=False |
| K10-T02 | PASS | Registered sender asks dues before verification | replies=['ACK', 'ASK_NAME'] shared=N verified=False |
| K10-T03 | PASS | Exact full name verifies | replies=['VERIFIED'] shared=N verified=True |
| K10-T04 | PASS | First name only verifies | replies=['VERIFIED'] shared=N verified=True |
| K10-T05 | PASS | Small typo tolerated (1 edit) | replies=['VERIFIED'] shared=N verified=True |
| K10-T06 | PASS | Honorifics ignored | replies=['VERIFIED'] shared=N verified=True |
| K10-T07 | PASS | Wrong name first attempt - retry | replies=['NAME_RETRY'] shared=N verified=False |
| K10-T08 | PASS | Wrong name second attempt - fail and callback | replies=['NAME_FAILED'] shared=N verified=False |
| K10-T09 | PASS | Unregistered sender asks dues | replies=['ACK', 'UNREGISTERED'] shared=N verified=False |
| K10-T10 | PASS | STOP honoured before verification | replies=['STOP_CONFIRMED'] shared=N verified=False |
| K10-T11 | PASS | STOP honoured from unregistered number too | replies=['STOP_CONFIRMED'] shared=N verified=False |
| K10-T12 | PASS | Hardship acknowledged before verification | replies=['HARDSHIP_ACK'] shared=N verified=False |
| K10-T13 | PASS | Verified dues query | replies=['BALANCE', 'BALANCE', 'ANYTHING_ELSE'] shared=Y verified=True |
| K10-T14 | PASS | Verified payment link | replies=['PAY_LINK'] shared=Y verified=True |
| K10-T15 | PASS | Verified statement | replies=['STATEMENT'] shared=Y verified=True |
| K10-T16 | PASS | Unverified receipt request asks name first | replies=['ACK', 'ASK_NAME'] shared=N verified=False |
| K10-T17 | PASS | Foreclosure quote is routed not served | replies=['ROUTED'] shared=N verified=True |
| K10-T18 | PASS | Dispute routed | replies=['ROUTED'] shared=N verified=True |
| K10-T19 | PASS | Payment claim - no proof yet | replies=['PAY_ASK_PROOF'] shared=N verified=True |
| K10-T20 | PASS | Payment claim - feed pending | replies=['CLAIM_PENDING'] shared=N verified=True |
| K10-T21 | PASS | Payment settled and allocated - receipt | replies=['RECEIPT'] shared=Y verified=True |
| K10-T22 | PASS | Edited screenshot - no receipt routed | replies=['PAY_NOT_MATCHED'] shared=N verified=True |
| K10-T23 | PASS | Reference mismatch - no receipt routed | replies=['PAY_NOT_MATCHED'] shared=N verified=True |
| K10-T24 | PASS | Needs time - ask for a date within 2 days | replies=['NT_ASK_DATE'] shared=N verified=True |
| K10-T25 | PASS | Needs time with 2 broken PTPs - push payment | replies=['BROKEN_PTP_PUSH'] shared=Y verified=True |
| K10-T26 | PASS | PTP in 3 days captured | replies=['PTP_CAPTURED'] shared=N verified=True |
| K10-T27 | PASS | PTP in 15 days captured for review | replies=['PTP_CAPTURED_REVIEW'] shared=N verified=True |
| K10-T28 | PASS | Callback request | replies=['CALLBACK_CREATED'] shared=N verified=False |
| K10-T29 | PASS | Abusive message ends conversation | replies=['END_CONVERSATION'] shared=N verified=True |
| K10-T30 | PASS | Injection attempt before verification - safe reply nothing shared | replies=['INJECTION_SAFE'] shared=N verified=False |
| K10-T31 | PASS | Wrong person on the number | replies=['WRONG_PERSON'] shared=N verified=False |
| K10-T32 | PASS | Similar different name rejected (Ramesh vs Rajesh) | replies=['NAME_RETRY'] shared=N verified=False |
| K10-T33 | PASS | Long surname tolerates 2 typos | replies=['VERIFIED'] shared=N verified=True |

### K13-operational-exceptions

PASS 25

| Case | Status | Description | Our result |
|---|---|---|---|
| K13-T01 | PASS | Needs time in B1 - first rebuttal | REBUTTAL/NONE/ |
| K13-T02 | PASS | B1 rebuttals exhausted (3) - accept no date | ACCEPT_NO_DATE/STOP_FOR_DAY/ |
| K13-T03 | PASS | B3 allows 7 - 6 used so rebut again | REBUTTAL/NONE/ |
| K13-T04 | PASS | B3 rebuttals exhausted (7) | ACCEPT_NO_DATE/STOP_FOR_DAY/ |
| K13-T05 | PASS | Pre-due allows only 1 rebuttal | ACCEPT_NO_DATE/STOP_FOR_DAY/ |
| K13-T06 | PASS | Outside contact window - acknowledge only no rebuttal | ACK_ONLY/STOP_FOR_DAY/ |
| K13-T07 | PASS | PTP within 2 days auto accepted | CAPTURE_PTP/PTP_HOLD/ |
| K13-T08 | PASS | PTP at 7 days still auto accepted | CAPTURE_PTP/PTP_HOLD/ |
| K13-T09 | PASS | PTP at 20 days - capture and supervisor | CAPTURE_PTP/PTP_HOLD/COLLECTIONS_SUPERVISOR |
| K13-T10 | PASS | 2 broken PTPs - no PTP solicitation push payment | PUSH_PAYMENT_NOW/NONE/ |
| K13-T11 | PASS | 2 broken - borrower offers PTP and does not pay - record no hold | RECORD_PTP_NO_HOLD/STOP_FOR_DAY/ |
| K13-T12 | PASS | 2 broken - borrower pays right away | VERIFY_PAYMENT/NONE/ |
| K13-T13 | PASS | Callback requested | CREATE_CALLBACK_TASK/CALLBACK_PENDING/COLLECTIONS_SUPERVISOR |
| K13-T14 | PASS | First callback attempt unanswered - retry in 2h | RETRY_CALLBACK_2H/CALLBACK_PENDING/COLLECTIONS_SUPERVISOR |
| K13-T15 | PASS | Second attempt missed - send we-tried message resume next day | SEND_MISSED_CALLBACK_MSG/STOP_FOR_DAY/ |
| K13-T16 | PASS | Callback answered - close task | CLOSE_CALLBACK_TASK/STOP_FOR_DAY/ |
| K13-T17 | PASS | Hardship with no date - 2 day pause | HOLD_2_DAYS/HARDSHIP_HOLD/ |
| K13-T18 | PASS | Hardship date in 3 days - hold until date | HOLD_UNTIL_DATE/HARDSHIP_HOLD/ |
| K13-T19 | PASS | Hardship date in 10 days - supervisor | HOLD_UNTIL_DATE/HARDSHIP_HOLD/COLLECTIONS_SUPERVISOR |
| K13-T20 | PASS | Dispute routed - no decision | CREATE_TICKET/STOP_FOR_DAY/COLLECTIONS_SUPERVISOR |
| K13-T21 | PASS | Waiver request routed - no commitment | CREATE_TICKET/STOP_FOR_DAY/COLLECTIONS_SUPERVISOR |
| K13-T22 | PASS | Complaint routed | CREATE_TICKET/STOP_FOR_DAY/COLLECTIONS_SUPERVISOR |
| K13-T23 | PASS | Abusive - end conversation and pause outreach | END_CONVERSATION_AND_TICKET/SUPERVISOR_HOLD/COLLECTIONS_SUPERVISOR |
| K13-T24 | PASS | Legal threat - end conversation | END_CONVERSATION_AND_TICKET/SUPERVISOR_HOLD/COLLECTIONS_SUPERVISOR |
| K13-T25 | PASS | Fraud claim - end conversation | END_CONVERSATION_AND_TICKET/SUPERVISOR_HOLD/COLLECTIONS_SUPERVISOR |

### K15-safety-load-replay

PASS 9, NOT_IMPLEMENTED 1, NOT_APPLICABLE 2

| Case | Status | Description | Our result |
|---|---|---|---|
| K15-T01 | PASS (report) | 50K daily decisions throughput in under 10 minutes | 50,000 reserve() decisions in 20.5s (scripts/bench_ledger.py 50000) |
| K15-T02 | NOT_APPLICABLE (report) | Response efficiency lift over fixed-cadence baseline | needs a full-scale run: scripts/policy_coverage.py --k15-full |
| K15-T03 | NOT_APPLICABLE (report) | Zero regulatory cap breaches across 50000 accounts | needs a full-scale run: scripts/policy_coverage.py --k15-full |
| K15-T04 | PASS (report) | 500 concurrent inbound chats throughput | CM-03 eval 500-concurrency bar: engine {'n': 500, 'p95_ms': 2664.14, 'errors': 0, 'cross_talk': 0}, http {'n': 500, 'p95_ms': 3193.19, 'errors': 0, 'cross_talk': 0} (scripts/run_cm03_eval.py, SIMULATED) |
| K15-T05 | PASS (report) | Zero account disclosures before identity verification | CM-03 eval disclosure bar: 0 |
| K15-T06 | PASS (report) | 100% of fake/edited UPI screenshots caught and blocked | CM-03 eval fake-catch bar: 127/127 |
| K15-T07 | PASS | Funnel stages reconciled (Eligible->Attempted->Delivered->Replied->Paid) | stages ['eligible', 'attempted', 'delivered_or_connected', 'meaningful_reply', 'verified_payment'] = [291, 291, 283, 8, 3] (300 borrowers x 3 days, SIMULATED) |
| K15-T08 | PASS | Stale DPD (>24h) or missing DPD safely halts outreach | missing DPD -> WAIT/DPD_INVALID; stale DPD -> WAIT/DPD_INVALID |
| K15-T09 | PASS | DPD band boundaries (29->30 B1 cap 3; 31 B2 cap 6) | bands 29/30/31 = ('B1', 'B1', 'B2'); 3rd/4th B1 sms, 4th/7th B2 sms -> ['SEND', 'WAIT', 'SEND', 'WAIT'] |
| K15-T10 | PASS | SMS delivered receipt does not count as read or reply | K21 score without/with SMS delivered = 55/55; channels/normalize.py keeps SMS read UNKNOWN: True (behaviour covered by tests/test_channels.py E04) |
| K15-T11 | NOT_IMPLEMENTED | Dynamic mid-campaign switch from unresponsive SMS to WhatsApp | no explicit 'switch SMS -> WhatsApp when unresponsive and WhatsApp reach is seen' rule; K09 learns channel per band x channel x slot from replies (Beta-Bernoulli), so the switch is emergent, not asserted |
| K15-T12 | PASS | Deterministic replay results given random seed | K15 replay seed 42 run twice: identical results |

### K21-propensity-intensity

PASS 20

| Case | Status | Description | Our result |
|---|---|---|---|
| K21-T01 | PASS | Pre-due good payer - likely self-cure | score 89 LOW ev=0.75 |
| K21-T02 | PASS | B1 replied but needs time | score 69 STANDARD ev=0.75 |
| K21-T03 | PASS | B2 technical bounce with healthy cash flow - persuadable | score 59 HIGH ev=1.00 |
| K21-T04 | PASS | B3 refuser with weak finances - clamps to 0 | score 0 LOW ev=1.00 |
| K21-T05 | PASS | B4 silent and unreachable | score 8 LOW ev=0.75 |
| K21-T06 | PASS | B1 baseline without SMS delivery (low evidence -> STANDARD) | score 55 STANDARD ev=0.50 |
| K21-T07 | PASS | Same as T06 with SMS delivered - SMS adds 0 (low evidence -> STANDARD) | score 55 STANDARD ev=0.50 |
| K21-T08 | PASS | B2 willing and responsive on 2 channels | score 90 LOW ev=0.75 |
| K21-T09 | PASS | Same as T08 but STOP - forced WAIT | score 90 WAIT ev=0.75 |
| K21-T10 | PASS | Persuadable under hardship hold - forced WAIT | score 59 WAIT ev=1.00 |
| K21-T11 | PASS | Persuadable under PTP hold - forced WAIT | score 55 WAIT ev=0.50 |
| K21-T12 | PASS | Boundary 70 is LOW | score 70 LOW ev=0.75 |
| K21-T13 | PASS | Boundary 60 is STANDARD | score 60 STANDARD ev=0.75 |
| K21-T14 | PASS | Boundary 40 is HIGH (low evidence -> STANDARD) | score 40 STANDARD ev=0.50 |
| K21-T15 | PASS | 38 is STANDARD | score 38 STANDARD ev=0.50 |
| K21-T16 | PASS | Boundary 25 is STANDARD | score 25 STANDARD ev=0.50 |
| K21-T17 | PASS | 20 is LOW | score 20 LOW ev=0.75 |
| K21-T18 | PASS | No AA consent is neutral (same as T06) (low evidence -> STANDARD) | score 55 STANDARD ev=0.50 |
| K21-T19 | PASS | Dispute lowers score | score 35 STANDARD ev=0.50 |
| K21-T20 | PASS | Score 54 with 3 of 4 signal groups keeps HIGH | score 54 HIGH ev=0.75 |

### K24-portfolio-intake

PASS 32

| Case | Status | Description | Our result |
|---|---|---|---|
| K24-C01 | PASS | L1001 -> ACCEPTED | got ('ACCEPTED', '') |
| K24-C02 | PASS | L1002 -> ACCEPTED | got ('ACCEPTED', '') |
| K24-C03 | PASS | L1003 -> ACCEPTED | got ('ACCEPTED', '') |
| K24-C04 | PASS | L1004 -> QUARANTINED INVALID_MOBILE | got ('QUARANTINED', 'INVALID_MOBILE') |
| K24-C05 | PASS | L1005 -> QUARANTINED MISSING_FIELD:borrower_name | got ('QUARANTINED', 'MISSING_FIELD:borrower_name') |
| K24-C06 | PASS | L1006 -> QUARANTINED INVALID_AMOUNT | got ('QUARANTINED', 'INVALID_AMOUNT') |
| K24-C07 | PASS | L1007 -> QUARANTINED DUPLICATE_LOAN_ID | got ('QUARANTINED', 'DUPLICATE_LOAN_ID') |
| K24-C08 | PASS | L1008 -> QUARANTINED INVALID_DATE | got ('QUARANTINED', 'INVALID_DATE') |
| K24-C09 | PASS | L1009 -> QUARANTINED INVALID_DPD | got ('QUARANTINED', 'INVALID_DPD') |
| K24-C10 | PASS | L1010 -> ACCEPTED | got ('ACCEPTED', '') |
| K24-C11 | PASS | L1011 -> ACCEPTED OUT_OF_SCOPE_DPD | got ('ACCEPTED', 'OUT_OF_SCOPE_DPD') |
| K24-C12 | PASS | L1012 -> ACCEPTED DPD_DUE_DATE_MISMATCH;CONSENT_NOT_PROVIDED | got ('ACCEPTED', 'DPD_DUE_DATE_MISMATCH;CONSENT_NOT_PROVIDED') |
| K24-C13 | PASS | L1013 -> QUARANTINED INVALID_AMOUNT | got ('QUARANTINED', 'INVALID_AMOUNT') |
| K24-C14 | PASS | duplicate loan_id: both copies quarantined | 2 copies |
| K24-C15 | PASS | mobile '+91 98765 43210' normalised | normalised |
| K24-C16 | PASS | budgeted set not specified: nothing budgeted, arming blocked | BUDGET_SET_NOT_SPECIFIED |
| K24-C17 | PASS | start skips Sunday 22 Nov -> Mon 23 Nov 08:00 | 2026-11-23 08:00 |
| K24-C18 | PASS | end date inclusive, no sends from 19:00 | 2026-12-21 before 19:00 |
| K24-C19 | PASS | out-of-scope L1011 (DPD -10) never eligible for budget | eligible=5 |
| K24-C20 | PASS | pool = Rs 10 x 5 budgeted loans | pool Rs 50.0 |
| K24-C21 | PASS | explicit selection: out-of-scope and quarantined loans ignored; pool = Rs 10 x 2 | budgeted=2 pool Rs 20.0 |
| K24-C22 | PASS | Rs 0.40 touch to C-1042 split equally across its 2 budgeted loans | paise {'L1001': 20, 'L1002': 20} |
| K24-C23 | PASS | C-1042 grouped as one contact unit with 2 loans | customers=5 |
| K24-C24 | PASS | merge L1001 -> UPDATED | got UPDATED |
| K24-C25 | PASS | merge L1002 -> UNCHANGED | got UNCHANGED |
| K24-C26 | PASS | merge L1003 -> REMOVED_BY_LENDER | got REMOVED_BY_LENDER |
| K24-C27 | PASS | merge L1010 -> UNCHANGED | got UNCHANGED |
| K24-C28 | PASS | merge L1011 -> UNCHANGED | got UNCHANGED |
| K24-C29 | PASS | merge L1012 -> UNCHANGED | got UNCHANGED |
| K24-C30 | PASS | merge L1014 -> ADDED | got ADDED |
| K24-C31 | PASS | updated loan keeps journey history | journey=['SMS delivered'] |
| K24-C32 | PASS | same file again: idempotent, nothing changes | version 2 |

### T01-outbound-templates

FAIL 300

Rules our K22 checker flags in his rows (kind: FORMAT = his text is fine, our checker does not recognise the form; POLICY = a real rule conflict for Product; NOT_BUILT = we lack the provider object; T01_DEFECT = inside his pack):

| Rule | Rows | Kind | Sample ids |
|---|---|---|---|
| PAY_AND_HELP | 240 | POLICY | `EM-B1-A-EN`, `EM-B1-A-HI`, `EM-B1-B-EN` |
| ACCOUNT_LAST4_ONLY | 130 | FORMAT | `EM-B1-A-EN`, `EM-B1-A-HI`, `EM-B1-B-EN` |
| PURPOSE_CONTENT | 96 | POLICY | `EM-B3-A-EN`, `EM-B3-A-HI`, `EM-B3-B-EN` |
| CHANNEL_REGISTRATION | 60 | NOT_BUILT | `VO-B1-A-EN`, `VO-B1-A-HI`, `VO-B1-B-EN` |
| IDENTITY_STAGE | 60 | FORMAT | `IV-B1-A-EN`, `IV-B1-A-HI`, `IV-B1-B-EN` |
| IGNORE_IF_PAID | 60 | POLICY | `EM-PRE_DUE-A-EN`, `EM-PRE_DUE-A-HI`, `EM-PRE_DUE-B-EN` |
| PLACEHOLDERS_ALLOWED | 60 | FORMAT | `EM-B1-A-EN`, `EM-B1-A-HI`, `EM-B1-B-EN` |
| LANGUAGE_PARITY | 20 | T01_DEFECT | `IV-B1-A-EN`, `IV-B1-A-HI`, `IV-B2-A-EN` |
| CONSEQUENCES_PERMITTED | 16 | POLICY | `EM-B1-F-EN`, `EM-B1-F-HI`, `EM-B2-F-EN` |

| Status | Our checker says | Rows | Template ids |
|---|---|---|---|
| FAIL | checklist: CHANNEL_REGISTRATION[NOT_BUILT] \| CHANNEL_REGISTRATION: sarvam_agent.name missing | 34 | `VO-B1-B-EN`, `VO-B1-B-HI`, `VO-B1-D-EN`, `VO-B1-D-HI`, `VO-B1-E-EN`, `VO-B1-E-HI` … (+28) |
| FAIL | checklist: PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PURPOSE_CONTENT[POLICY] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 28 | `SM-B3-A-EN`, `WH-B3-A-EN`, `SM-B3-A-HI`, `WH-B3-A-HI`, `WH-B3-B-EN`, `WH-B3-B-HI` … (+22) |
| FAIL | checklist: PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 26 | `SM-B1-A-EN`, `WH-B1-A-EN`, `SM-B1-A-HI`, `WH-B1-A-HI`, `WH-B1-B-EN`, `WH-B1-B-HI` … (+20) |
| FAIL | checklist: PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PLACEHOLDERS_ALLOWED[FORMAT], PURPOSE_CONTENT[POLICY] \| PAY_AND_HELP: no helpline ({{helpline}} mis | 20 | `EM-B3-A-EN`, `EM-B3-A-HI`, `EM-B3-B-EN`, `EM-B3-B-HI`, `EM-B3-C-EN`, `EM-B3-C-HI` … (+14) |
| FAIL | checklist: PAY_AND_HELP[POLICY], PURPOSE_CONTENT[POLICY] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 20 | `SM-B3-B-EN`, `SM-B3-B-HI`, `SM-B3-C-EN`, `SM-B3-C-HI`, `SM-B3-D-EN`, `SM-B3-D-HI` … (+14) |
| FAIL | checklist: PAY_AND_HELP[POLICY], IDENTITY_STAGE[FORMAT], PURPOSE_CONTENT[POLICY] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 20 | `IV-B3-B-EN`, `IV-B3-B-HI`, `IV-B3-C-EN`, `IV-B3-C-HI`, `IV-B3-D-EN`, `IV-B3-D-HI` … (+14) |
| FAIL | checklist: PAY_AND_HELP[POLICY] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 18 | `SM-B1-B-EN`, `SM-B1-B-HI`, `SM-B1-C-EN`, `SM-B1-C-HI`, `SM-B1-D-EN`, `SM-B1-D-HI` … (+12) |
| FAIL | checklist: PAY_AND_HELP[POLICY], IDENTITY_STAGE[FORMAT] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 18 | `IV-B1-B-EN`, `IV-B1-B-HI`, `IV-B1-C-EN`, `IV-B1-C-HI`, `IV-B1-D-EN`, `IV-B1-D-HI` … (+12) |
| FAIL | checklist: PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PLACEHOLDERS_ALLOWED[FORMAT] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 16 | `EM-B1-A-EN`, `EM-B1-A-HI`, `EM-B1-B-EN`, `EM-B1-B-HI`, `EM-B1-C-EN`, `EM-B1-C-HI` … (+10) |
| FAIL | checklist: PAY_AND_HELP[POLICY], IGNORE_IF_PAID[POLICY], ACCOUNT_LAST4_ONLY[FORMAT] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 14 | `SM-PRE_DUE-A-EN`, `WH-PRE_DUE-A-EN`, `SM-PRE_DUE-A-HI`, `WH-PRE_DUE-A-HI`, `WH-PRE_DUE-B-EN`, `WH-PRE_DUE-B-HI` … (+8) |
| FAIL | checklist: PAY_AND_HELP[POLICY], IGNORE_IF_PAID[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PLACEHOLDERS_ALLOWED[FORMAT] \| PAY_AND_HELP: no helpline ({{helpline}} miss | 10 | `EM-PRE_DUE-A-EN`, `EM-PRE_DUE-A-HI`, `EM-PRE_DUE-B-EN`, `EM-PRE_DUE-B-HI`, `EM-PRE_DUE-C-EN`, `EM-PRE_DUE-C-HI` … (+4) |
| FAIL | checklist: PAY_AND_HELP[POLICY], IGNORE_IF_PAID[POLICY] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 10 | `SM-PRE_DUE-B-EN`, `SM-PRE_DUE-B-HI`, `SM-PRE_DUE-C-EN`, `SM-PRE_DUE-C-HI`, `SM-PRE_DUE-D-EN`, `SM-PRE_DUE-D-HI` … (+4) |
| FAIL | checklist: PAY_AND_HELP[POLICY], IGNORE_IF_PAID[POLICY], IDENTITY_STAGE[FORMAT] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 10 | `IV-PRE_DUE-B-EN`, `IV-PRE_DUE-B-HI`, `IV-PRE_DUE-C-EN`, `IV-PRE_DUE-C-HI`, `IV-PRE_DUE-D-EN`, `IV-PRE_DUE-D-HI` … (+4) |
| FAIL | checklist: IGNORE_IF_PAID[POLICY], CHANNEL_REGISTRATION[NOT_BUILT] \| IGNORE_IF_PAID: T1/pre-due reminder must say to ignore it if already paid | 10 | `VO-PRE_DUE-B-EN`, `VO-PRE_DUE-B-HI`, `VO-PRE_DUE-C-EN`, `VO-PRE_DUE-C-HI`, `VO-PRE_DUE-D-EN`, `VO-PRE_DUE-D-HI` … (+4) |
| FAIL | checklist: PAY_AND_HELP[POLICY], IDENTITY_STAGE[FORMAT], LANGUAGE_PARITY[T01_DEFECT] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 4 | `IV-B1-A-EN`, `IV-B1-A-HI`, `IV-B2-A-EN`, `IV-B2-A-HI` |
| FAIL | checklist: LANGUAGE_PARITY[T01_DEFECT], CHANNEL_REGISTRATION[NOT_BUILT] \| LANGUAGE_PARITY: placeholders differ from the hi version: ['loan_last4'] | 4 | `VO-B1-A-EN`, `VO-B2-A-EN`, `VO-B3-A-EN`, `VO-B4-A-EN` |
| FAIL | checklist: LANGUAGE_PARITY[T01_DEFECT], CHANNEL_REGISTRATION[NOT_BUILT] \| LANGUAGE_PARITY: placeholders differ from the en version: ['loan_last4'] | 4 | `VO-B1-A-HI`, `VO-B2-A-HI`, `VO-B3-A-HI`, `VO-B4-A-HI` |
| FAIL | checklist: CONSEQUENCES_PERMITTED[POLICY], CHANNEL_REGISTRATION[NOT_BUILT] \| CONSEQUENCES_PERMITTED: REMINDER may not mention late_fee | 4 | `VO-B1-C-EN`, `VO-B1-C-HI`, `VO-B2-C-EN`, `VO-B2-C-HI` |
| FAIL | checklist: PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PLACEHOLDERS_ALLOWED[FORMAT] \| PAY_AND_HELP: reminder has no way to pay ({{payment_link}} missing) | 4 | `EM-B1-D-EN`, `EM-B1-D-HI`, `EM-B2-D-EN`, `EM-B2-D-HI` |
| FAIL | checklist: CONSEQUENCES_PERMITTED[POLICY], PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PLACEHOLDERS_ALLOWED[FORMAT] \| CONSEQUENCES_PERMITTED: tier 2 may n | 4 | `EM-B1-F-EN`, `EM-B1-F-HI`, `EM-B2-F-EN`, `EM-B2-F-HI` |
| FAIL | checklist: PAY_AND_HELP[POLICY], IDENTITY_STAGE[FORMAT], LANGUAGE_PARITY[T01_DEFECT], PURPOSE_CONTENT[POLICY] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 4 | `IV-B3-A-EN`, `IV-B3-A-HI`, `IV-B4-A-EN`, `IV-B4-A-HI` |
| FAIL | checklist: PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PLACEHOLDERS_ALLOWED[FORMAT], PURPOSE_CONTENT[POLICY] \| PAY_AND_HELP: reminder has no way to pay ({ | 4 | `EM-B3-D-EN`, `EM-B3-D-HI`, `EM-B4-D-EN`, `EM-B4-D-HI` |
| FAIL | checklist: PAY_AND_HELP[POLICY], IGNORE_IF_PAID[POLICY], IDENTITY_STAGE[FORMAT], LANGUAGE_PARITY[T01_DEFECT] \| PAY_AND_HELP: no helpline ({{helpline}} missing) | 2 | `IV-PRE_DUE-A-EN`, `IV-PRE_DUE-A-HI` |
| FAIL | checklist: IGNORE_IF_PAID[POLICY], LANGUAGE_PARITY[T01_DEFECT], CHANNEL_REGISTRATION[NOT_BUILT] \| IGNORE_IF_PAID: T1/pre-due reminder must say to ignore it if a | 2 | `VO-PRE_DUE-A-EN`, `VO-PRE_DUE-A-HI` |
| FAIL | checklist: PAY_AND_HELP[POLICY], IGNORE_IF_PAID[POLICY], ACCOUNT_LAST4_ONLY[FORMAT], PLACEHOLDERS_ALLOWED[FORMAT] \| PAY_AND_HELP: reminder has no way to pay ({{ | 2 | `EM-PRE_DUE-D-EN`, `EM-PRE_DUE-D-HI` |
| FAIL | checklist: CONSEQUENCES_PERMITTED[POLICY], PAY_AND_HELP[POLICY] \| CONSEQUENCES_PERMITTED: tier 2 may not mention bureau | 2 | `SM-B2-F-EN`, `SM-B2-F-HI` |
| FAIL | checklist: CONSEQUENCES_PERMITTED[POLICY], PAY_AND_HELP[POLICY], ACCOUNT_LAST4_ONLY[FORMAT] \| CONSEQUENCES_PERMITTED: tier 2 may not mention bureau | 2 | `WH-B2-F-EN`, `WH-B2-F-HI` |
| FAIL | checklist: CONSEQUENCES_PERMITTED[POLICY], PAY_AND_HELP[POLICY], IDENTITY_STAGE[FORMAT] \| CONSEQUENCES_PERMITTED: tier 2 may not mention bureau | 2 | `IV-B2-F-EN`, `IV-B2-F-HI` |
| FAIL | checklist: CONSEQUENCES_PERMITTED[POLICY], CHANNEL_REGISTRATION[NOT_BUILT] \| CONSEQUENCES_PERMITTED: REMINDER may not mention bureau | 2 | `VO-B2-F-EN`, `VO-B2-F-HI` |

<!-- END GENERATED -->
