# backend/ — K06 guardrails + executor on the K03 spine

Python service that decides whether a touch **may** be sent, reserves it atomically, dispatches it once, and cancels queued work when the borrower's situation changes. Rules come from [`../policy`](../policy/README.md); borrowers, loans, EMIs, consent and payments come from the **K03 spine**.

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                            # K01 + K13 cases, K06 ledger/executor/API/SupervisorFSM, K03 spine + K15 replay, K09
uvicorn kairos_backend.api:app --reload    # http://127.0.0.1:8000/docs
python scripts/bench_ledger.py 50000       # 50k decisions on K03's synthetic dataset
```

K03 / K15 scripts (Varun): `python scripts/spine_report.py`, `scripts/make_dataset.py`, `scripts/load_dataset.py`, `scripts/run_replay.py` — see [`spine/README.md`](spine/README.md).

K09 replay (SIMULATED): `python3 scripts/run_k09_replay.py --borrowers 600 --days 7` — see [K09 replay](#k09-replay-simulated).

Environment: `KAIROS_POLICY_DIR` (default `../policy`), `KAIROS_DB` (default `backend/kairos.db`).

## Layout

| Package | Task | Owner | What it does |
|---|---|---|---|
| `spine/` | K03 | Varun | Borrowers, consents, loans, EMIs (obligations), payments, events. Money in paise. Dataset importer + synthetic 50k generator. |
| `replay/` | K15 | Varun | Campaign replay: baseline vs learning policy, dev simulator, 500-chat load test. |
| `kairos_backend/` | K06 | Rehan | Gate, ledger, supervisor (`executor.Supervisor` + `supervisor.SupervisorFSM`), executor, API — reads the spine, never copies it. |
| `kairos_backend/` | K09 | Rehan / Varun | `contracts` (shared types), `learner` (Beta–Bernoulli band × channel × slot), `outcomes` (24 h reply window → one outcome per touch), `borrower_context` (engagement × recency multipliers), `intent` (rules + gpt-6-luna), `propensity` (demo). |
| `replay/k09.py`, `replay/cn10.py` | K09 | Rehan | K09 replay on the full stack; CN10 cohort-cadence baseline. |

## How K06 uses the spine

One SQLite file holds both. K03 owns *who / what is owed / what was paid*; K06 owns *campaigns, holds, reservations, audit*.

| Gate fact | Comes from |
|---|---|
| DPD + band | Live from the loan's oldest unpaid EMI due date (K03 rule). Negative = PRE_DUE. Dataset loan with no DPD → HOLD. |
| DPD fresh? | Hours since the spine was last synced (`mark_spine_synced`; `start_day` in simulations) or the loan's `dpd_as_of`. > 24 h → HOLD. |
| Consent / opt-out | K03 `consents` (per channel). STOP sets `opted_out` on every channel; opt-in is per channel. |
| Obligation cleared | That EMI's `amount_paid_paise >= amount_due_paise`. |
| Verified overdue | EMI past its due date and not fully paid. |
| Pool | `budget_per_case_paise` × loans enrolled in the campaign. |
| Collection target | Verified paise collected ÷ overdue at enrolment; loans cured ÷ loans enrolled. Reversals roll back. |

K06 writes back to the spine: `touch_sent` when a send is confirmed, `reply_received` on a reply, `consent_changed` on STOP/opt-in, and payments go through K03's `apply_payment` / `reverse_payment` (one EMI of one loan, never over the amount due, a bank reference used once).

## Flow

```
agent recommendation ──► Supervisor.decide() ──► Ledger.reserve()  ── one write transaction ──┐
                                                   │ facts from K03 spine + K06 holds/counters  │
                                                   │ Gate.evaluate(): K01 rules in policy order │
                                                   └► SEND → touch RESERVED + cost reserved ◄──┘
Executor.dispatch() ──► Ledger.begin_dispatch()  (re-check at send time; else CANCEL + release)
                    ──► Provider.send()          ACCEPTED → SENT · REJECTED → FAILED (release)
                                                 no answer → UNCERTAIN → resolve_uncertain() asks the provider
borrower events (reply, call >10 s, STOP, payment, reversal, K13 exceptions) ──► cancel queued work in scope
```

## Guarantees and how they're tested

| K06 "done when" | Mechanism | Test |
|---|---|---|
| Parallel workers stay within caps and budget | `BEGIN IMMEDIATE` around facts → Gate → insert; reserved touches count immediately | `test_parallel_workers_*` (repeated 20× without a flake) |
| Same instruction never dispatched twice | Stable `instruction_id`; RESERVED → DISPATCHING once | `test_same_instruction_reserved_once`, `test_dispatch_once_*` |
| Check uncertain sends before retry | UNCERTAIN keeps its cap slot and cost; `resolve_uncertain` asks the provider | `test_uncertain_*` |
| STOP / verified payment cancel pending work within scope | STOP → all channels; payment → that EMI only; partial payment keeps it open | `test_stop_*`, `test_verified_payment_clears_only_that_emi`, `test_partial_payment_keeps_emi_open` |
| Reconcile actual charge once | `reconciled` flag | `test_reconcile_exactly_once` |
| Zero cap breaches | Gate at approval + send time; independent `cap_breaches()` recount | concurrency tests, K03 dataset test, `/cap-breaches` |
| 50k decisions < 10 min | ~51 s for 50,000 full atomic decisions on K03's synthetic dataset | `scripts/bench_ledger.py` |

## Interpretations (flag if wrong)

- **PTP hold is borrower-wide** — K01 says "no activity on any channel".
- **K13 holds not in K01's order** (`CALLBACK_PENDING`, `SUPERVISOR_HOLD`) run right after `HARDSHIP_HOLD` until Product places them (`gate.EXTRA_RULES_AFTER`).
- **Verified payment cancels that EMI's queued touches immediately**, even during a PTP hold, and ends the PTP hold if it was for that EMI.
- **Daily pacing is a hard limit** (floored to whole paise). Toggle with setting `enforce_daily_pacing`.
- **Weekly caps use a rolling 7 days** (stricter than a calendar week).
- **Any hold cancels the borrower's queued touches** so their budget returns to the pool.
- **A case = a loan** (K01 "accounts"): pool and cure rate count enrolled loans.
- `budget_per_case` and collection targets are K01 REQUIRED_INPUTs — supplied per campaign, never defaulted.

## K09 on the spine

| Piece | What it uses from the spine / K06 |
|---|---|
| `SupervisorFSM.plan` | One plan per EMI (spine obligation). `band=None` → live band from the loan's oldest unpaid EMI. Each candidate is checked with `Ledger.preview`; the learner ranks only eligible ones; `Ledger.reserve` is atomic. CRM envelopes carry the real `loan_id` and the `obligation_id`. |
| `Ledger.preview(ins, exclude=)` | `exclude` names one live touch to leave out of caps / same-channel gap (as `begin_dispatch` does for itself) and returns its reserved paise to the budget when it is the same campaign and day — so re-planning the identical stable-id touch is an idempotent replay, never a self-block. |
| `SupervisorFSM.dispatch` | `Executor.dispatch` → `begin_dispatch` re-check; a payment (K03 `apply_payment` clears the EMI) or STOP (spine consents opted out) before dispatch → `CANCEL` with the gate rule. |
| Learner / outcomes | Costs in integer paise (`contracts.SYNTHETIC_COSTS` = `replay.settings.COST_PAISE`, one table); ranking by expected replies per rupee. Posterior mean in PRODUCTION; seeded Thompson only in SANDBOX / SIMULATOR. `decision_scores` with `model_version`. |

## K09 replay (SIMULATED)

```bash
python3 scripts/run_k09_replay.py --borrowers 600 --days 7                 # CN10 baseline vs K09
python3 scripts/run_k09_replay.py --borrowers 600 --days 7 --baseline both # + the K15 placeholder schedule
python3 scripts/run_k09_replay.py --no-explore --no-context --no-drift --check-determinism
```

Every arm runs through the same stack (spine import → SupervisorFSM → executor → K15 DEV simulator →
ledger events → outcome finalizer) on the same borrowers: K03's synthetic generator (same seed → same
borrowers as `make_dataset.py`), with Bounce Date anchored at the replay start so the spine's live
DPD equals the file DPD on day 1 (`--dataset` uses a K03 file as-is). Caps are matched (K01 + K15
placeholder caps as lender override `replay/k15_lender_caps.json` + K15's 2-day cross-channel gap as a
pre-filter), and so is money: the pool defaults to what the baseline spends in an unconstrained
calibration run. Report: `reports/k09_replay_report.json` (gitignored).

**Baselines.** `cn10` (default) is CreditNirvana's own *Personal Loan — 10-Day Post-Due Digital
Collections Strategy* (HDFC project): cohorts C1–C5 from the dataset's Risk Tagging (else the bounce
string), a fixed 10-day calendar per cohort with message tiers T1–T4 (`CN10_{WA|SMS|IVR}_T{n}_{EN|HI}`
templates), repeating after day 10 (setting in `replay/cn10.py`). It is a **stand-in until the
official fixed-cadence policy file (K02) arrives**. Same-day multi-channel steps are planned one by
one and the matched 1/day cap blocks the extras, exactly as for K09. CN's IVR is a one-way blast; the
DEV simulator lets IVR produce replies and the replay follows the simulator (flagged in the report).
`k15` is the K15 placeholder schedule.

## Voice engine (K17/K18)

Kairos side of the Sarvam voice bots: **which** bot / voice / language a call uses, and what Kairos
does with the bot's post-call disposition. Selection never decides **whether** a call may happen —
that stays with the K01 Gate (Supervisor → `Ledger.reserve`). Everything here is SIMULATED until a
live Sarvam webhook is wired (envelopes say `SIMULATED` unless `KAIROS_VOICE_PROVENANCE` is set).

| File | What |
|---|---|
| `kairos_backend/voice/registry.json` | Six use cases → Sarvam agent id + default voice (Ritu / Advait internal ids), supported languages, state → language map, lender overrides, A/B experiment (off). Registry version `0.2.0`, DRAFT. |
| `kairos_backend/voice/registry.py` | `select_bot(borrower, obligation, context)` (pure, deterministic) and `inputs_from_ledger()` (spine + K06 state → the three dicts). |
| `kairos_backend/voice/dispositions.py` | Unified post-call schema, strict parsing, PRD report ordinal. |
| `../policy/K17-voice-dispositions/thresholds.json` | Confidence thresholds, PTP max days, 48 h payment-claim pause, meaningful-reply set. `0.1.0-draft`, pending Ojas. |
| `kairos_backend/voice/ingest.py` | `decide()` (pure) + `VoiceIngester.ingest(payload)` (ledger effects, reward, CRM envelope). |
| `kairos_backend/voice/store.py` | Voice-only tables: `voice_calls`, `voice_preferences`, `voice_payment_claims`, `voice_followups`, `voice_contact_flags`. |

API: `POST /webhooks/sarvam/call-end` (idempotent by call id; optional `SARVAM_WEBHOOK_SECRET` →
header `X-Kairos-Webhook-Secret`), `GET /voice/registry`, `POST /voice/select` (`{borrower_id, at?,
obligation_id?, touch_id?, lender_name?, human_approved?}`), `GET /voice/calls/{call_id}`.

**Selection.** Use case: hardship (only with `human_approved`) → `HARDSHIP_TRIAGE`; callback pending →
`CALLBACK`; promise date reached → `PTP_FOLLOW_UP`; else by band (PRE_DUE → `PRE_DUE_REMINDER`,
B1 → `EMI_REMINDER_EARLY`, B2/B3 → `MID_BUCKET_RESOLUTION`; B4 → no bot). Language: explicit
preference → K03 `borrowers.language` (except the schema default `en`, which every import writes) →
language observed by an earlier bot → state map → Hindi; an unsupported language always ends in Hindi.
Voice: registry default → A/B arm (sha256(borrower_id|experiment_id) mod 2, when enabled) → lender
override → borrower preference. Call-start variables: `user_name` (first name only), `lender_name`,
`emi_due_date` / `max_ptp_date` (spoken, "24 November"), `call_date` (YYYY-MM-DD IST), `touch_id`,
`preferred_language`, plus `overdue_days` (MID_BUCKET), `promise_date` (PTP_FOLLOW_UP),
`callback_reason` (CALLBACK). `check_variables()` blanks anything carrying a 3+ digit run, a currency
marker, `@` or `+<digit>`; only `call_date`, `overdue_days` and `touch_id` have fixed formats.

**Thresholds** (fail closed):

| Disposition | Accept at | Accepted → | Below → |
|---|---|---|---|
| STOP_REQUESTED | 0.5 | `Ledger.record_stop` (all channels) | supervisor hold + handoff/review |
| HARDSHIP | 0.5 | K13 hardship hold + handoff | review ticket |
| ABUSIVE / DISPUTE | 0.6 | K13 (`apply_exception`) + handoff | review ticket |
| WRONG_PERSON / THIRD_PARTY | 0.8 | voice consent withdrawn (voicebot + ivr), contact UNVERIFIED, re-verify ticket | review ticket |
| PTP / PARTIAL_PTP | 0.8 and date within max days (PRE_DUE/B1 7, B2 5, B3/B4 3; PTP_FOLLOW_UP calls 3) | K13 PTP hold through the promise date; `PTP_FOLLOW_UP` due the day after | downgraded to NEEDS_TIME_NO_DATE (no hold) |
| ALREADY_PAID | 0.6 | claim pending; ledger `stop_for_day` rows (trigger `PAYMENT_CLAIM_PENDING`) for every day the 48 h touch; **never** a payment | review ticket |
| CALLBACK_REQUESTED | 0.7 and `callback_at` future + inside 08:00–19:00 | `Supervisor.decide` voicebot touch; refused → WAIT with the gate's reason | review ticket |
| HUMAN_REQUESTED | 0.6 | K13 CALLBACK_REQUEST (callback task + pause) | UNCLEAR → review |
| other (PAY_NOW_LINK, NEEDS_TIME_NO_DATE, REFUSED_TO_PAY, LANGUAGE_BARRIER) | 0.6 | intent recorded (LANGUAGE_BARRIER stores the observed language) | UNCLEAR → review |
| unknown enum | — | UNCLEAR, confidence 0 → review, no automated action | |

Learner reward: `OutcomeFinalizer.record_reply(meaningful=True)` only for PTP, PARTIAL_PTP,
PAY_NOW_LINK, ALREADY_PAID, NEEDS_TIME_NO_DATE, CALLBACK_REQUESTED, HARDSHIP, DISPUTE,
HUMAN_REQUESTED at ≥ 0.6 (after any downgrade). Reach (call connected → touch DELIVERED,
`record_reach`; answered > 10 s → `Ledger.record_call`) is recorded separately and is never a reply.

### Context for Claude / how to continue

- **Read first:** `voice/ingest.py` docstring, then `thresholds.json`. `decide()` is pure — change
  behaviour in the JSON (accept_at / accept / below) before touching code; an action name the code
  does not implement raises.
- **Ledger semantics are untouched.** Effects go through `record_stop`, `apply_exception`,
  `record_response`, `record_call`, `record_provider_status`, `Supervisor.decide`. Where no public
  ledger method exists the ingester uses the same private primitives `apply_exception` and the HITL
  module use (`_set_state`, `_cancel_queued`, `_ticket`, `_audit`) inside `ledger.tx()`.
- **HITL (k06/hitl-control, unmerged):** pass `hitl=HITL(ledger)` to `VoiceIngester` (the API picks
  up a module-level `hitl` if one exists) and handoffs call `hitl.act(..., "handoff", role="operator")`.
  Without it, `LedgerHandoff` places a SUPERVISOR_HOLD + `HUMAN_HANDOFF` ticket; a person clears it
  with `/control/supervisor-clear/{borrower_id}`.
- **Interpretations to confirm with Ojas:** (1) any voice reply triggers K01 stop-for-day, so a
  same-day callback is refused (`STOP_FOR_DAY`); (2) a callback more than 24 h after the last LMS
  refresh fails K01 `DPD_VALID` at request time — it is kept in `voice_followups` and
  `select_bot` picks `CALLBACK` on that day; (3) the 48 h claim pause rounds up to whole local
  days; (4) wrong person withdraws voice consent only (SMS/WhatsApp to the same number continue);
  (5) DISPUTE handoff pauses outreach (K13 alone would let it continue); (6) B4 has no voice bot.
- **Not done:** no Sarvam client places calls from here (agents are configured in the Sarvam
  console / MCP); `resolve_payment_claims(now)` and `due_followups(day)` need a scheduler tick;
  the gate template `VB_KAIROS_CALLBACK` (voicebot, `hi`) must be registered via `PUT /templates`.
- Tests: `tests/test_voice_engine.py` (every threshold both sides of its boundary, idempotency,
  language / voice / A/B, no PII in variables, API).
