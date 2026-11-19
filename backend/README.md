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


### Multi-seed result (CM-02 check, SIMULATED)

1,500 borrowers × 14 days, WhatsApp drift from day 7, budget matched to CN10's spend, K15 caps on both arms, K09 ranking by posterior mean (the default; `--explore` adds Thompson draws):

| Seed | CN10 replies / ₹1,000 | K09 replies / ₹1,000 | Change | Verified payments CN10 → K09 | Cap breaches |
|---|---|---|---|---|---|
| 7 | 56.18 | 78.30 | +39.4% | 35 → 60 | 0 / 0 |
| 42 | 58.79 | 74.54 | +26.8% | 53 → 62 | 0 / 0 |
| 99 | 65.27 | 77.85 | +19.3% | 45 → 62 | 0 / 0 |
| 2026 | 64.87 | 87.91 | +35.5% | 53 → 69 | 0 / 0 |

Mean +30.2%, minimum +19.3%. With `--explore` (Thompson) the same seeds give +8.1%, +10.6%, −12.4%, +2.9%, so exploration is left to SANDBOX/SIMULATOR learning runs and the comparison uses the production ranking. Reproduce one seed:

```bash
python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --baseline both
```

## Auto-churn engine (K27)

`kairos_backend/churn/` is CreditNirvana's event-driven cohort and risk engine (CN "Collection
Orchestration Engine" PRD v3): live outcomes from **every** channel (SMS, WhatsApp, email, IVR, the
Sarvam voicebot, payments, CM-03 WhatsApp intents, K13 holds) move a borrower between cohorts, and so
between omnichannel journeys. Policy (all DRAFT): [`../policy/K27-auto-churn`](../policy/K27-auto-churn/README.md).
Sarvam findings: [`../docs/sarvam-campaigns-notes.md`](../docs/sarvam-campaigns-notes.md).

```
normalized event (any channel) ──► dedupe by event_id ──► fold into features (older events never overwrite newer)
   ──► RISK engine (risk_rules.json: tier LOW..CRITICAL, cooldown 48 h, hysteresis 120 h, hardship freeze)
   ──► COHORT engine (reassign_rules.json: STOP / paid / hardship / K13 hold ALWAYS win → override → rules → pin)
   ──► journey entry (CN10: continue at DPD cycle day; behaviour journeys: restart at day 1)
   ──► LIFECYCLE (stop rules: ACTIVE, PAUSED_PTP, PAUSED_CLAIM, PAUSED_HOLD, HARDSHIP_HUMAN,
                  EXHAUSTED_CAMPAIGN, STOPPED_OPT_OUT, PAID_EXIT; transition table churn/fsm.py)
   ──► LAYER rules (L1 SMS/email · L2 WhatsApp · L3 IVR/voicebot · L4 human)
daily tick ──► PTP due (KEPT / BROKEN), hold / claim / pin expiry, tick rules, cohort-size snapshot
journey step of the day ──► allowed_channels(borrower, day) ──► K09 ranks (Rechurn / RL v2) ──► K06 Supervisor gates
```

| Module | What |
|---|---|
| `dsl.py` | Condition language: `= ≠ > < ≥ ≤ contains between in not_in exists`, `all/any/not`, PRD left-to-right `chain`; ordinal status fields; scope by campaign / cohort / region |
| `state.py` | `ChurnEvent` (kinds: delivery, reply, stop, opt_in, call, intent, ptp, payment, hold, attempt), `BorrowerState.apply` (time-ordered, per-touch max level, SMS read never tracked, STOP is not a reply), lazy `Features` + `FEATURE_CATALOGUE` |
| `cohorts.py` | `CohortSet`: first-match membership, `UNASSIGNED` live orphans, coverage (counts, overlaps, orphan ids), frozen orphan snapshot |
| `risk_rules.py` / `reassign_rules.py` | The two independent rule engines |
| `journeys.py` | Journeys (CN10 C1–C5 verbatim + behaviour journeys), layer gating, layer rules, `recommend_layer` acceptance, stop rules + `stop_policy` hook |
| `fsm.py` | Lifecycle `STATES` and `TRANSITIONS` (terminal states exit only on their own trigger) |
| `engine.py` | `ChurnEngine`: enroll (from K03 spine), process, tick, allowed_channels, recommend_layer, maker-checker overrides, manual schedule, start_campaign, borrower, trace, coverage, analytics (by campaign / cohort / region / layer) |
| `sarvam_export.py` | DRY RUN: `upload_cohort` (only borrowers with a reserved voice touch; `user_identifier` = `touch_id` = the ledger `instruction_id`, others listed as `skipped` with the gate's reason; phone stays a `PHONE_REF:` token, resolved at dial time outside the repo) + `configure_campaign` bodies with a K01-safe retry plan, CN AICC gate replies, Sarvam webhook → normalized events |
| `api.py` | `/churn/*` routes (mounted in `kairos_backend.api`) |

API: `GET/PUT /churn/cohorts`, `GET /churn/coverage`, `POST /churn/enroll`, `GET /churn/borrowers/{id}`,
`GET /churn/borrowers/{id}/trace`, `GET /churn/borrowers/{id}/allowed?day=`, `POST
/churn/borrowers/{id}/recommend-layer`, `POST /churn/events`, `POST /churn/tick`, `GET /churn/analytics`
(`?campaign=&region=&cohort=&layer=`), `POST /churn/overrides`, `POST /churn/overrides/{id}/decision`,
`POST /churn/manual`, `GET /churn/fsm`, `POST /churn/sarvam/webhook`, `POST /churn/sarvam/gate`,
`GET /churn/sarvam/export?day=`. Env `KAIROS_CHURN_DB` (default `backend/kairos_churn.db`, gitignored).
Actor via `X-Kairos-Actor` header (demo only, not real auth).

Example event (`POST /churn/events`):

```json
{"event_id": "wa:INS-1:reply:abc", "borrower_id": "BRW-SL100001", "kind": "intent", "channel": "whatsapp",
 "occurred_at": "2026-11-23T10:05:00+05:30", "intent": "PTP", "ptp_date": "2026-11-26", "provenance": "SIMULATED"}
```

Replay (SIMULATED): `python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --journeys both`
runs CN10 (unchanged), K09 alone and K09 + K27 on the same seed, borrowers, budget and caps, using only
ledger-visible outcomes (delivery, reply, STOP, claim, verified payment).

| Seed (1,500 × 14 days, matched budget/caps) | CN10 replies/₹1k | K09 | K09 + K27 | STOP opt-outs CN10 / K09 / K09+K27 | Verified payments | Cap breaches |
|---|---|---|---|---|---|---|
| 42 | 58.79 | 74.54 (+26.8%) | 67.00 (+14.0%) | 15 / 17 / 17 | 53 / 62 / 51 | 0 / 0 / 0 |
| 7 | 56.18 | 78.30 (+39.4%) | 63.69 (+13.4%) | 11 / 24 / 12 | 35 / 60 / 47 | 0 / 0 / 0 |

Reading (SIMULATED): K27's default journeys keep CN10's contact calendar (a day with no step = no contact,
~4,000 waits) and gate voice by layer, so K09 has fewer days to spend on: replies/₹ stays above CN10 but
below K09 alone. On seed 7 it halves K09's STOP opt-outs back to CN10's level. The DEV simulator produces no
PTP / hardship / dispute outcomes, so those cohorts are exercised by tests, not by this replay. Tuning the
journeys (e.g. more step days for WhatsApp-responsive borrowers) is open; never tune against the simulator's
hidden parameters.

### Context for Claude / how to continue

- **Read first:** `policy/K27-auto-churn/README.md` (open decisions defaulted, all DRAFT), `churn/engine.py`
  docstring (pipeline order), `tests/test_churn.py` (one test per behaviour).
- **Determinism:** no wall clock or randomness inside decisions: `at` = max(event time, last event time),
  `as_of` = max(event day, last tick). Same events + same policy files ⇒ same transitions (tested).
- **Never** let an override, pin, RL recommendation or journey beat STOP / verified payment / hardship /
  K13 holds — `reassign_rules.json` precedence and the lifecycle FSM enforce it; keep that order.
- **Interfaces for K09 RL v2 (`k09/rl-v2`):** `ChurnEngine.allowed_channels(borrower_id, day, consented)`
  (channels ordered step-first, `avoid_slots`, step purpose/tier/intensity), `recommend_layer(borrower_id,
  {"layer", "source", "recommendation_id"}, {"n_obs", "expected_lift"})` → accepted / rejected with a rule
  id, and `ChurnEngine(stop_policy=fn)` where `fn(features, state) -> {"budget": int, "source": str}` is
  the learned no-response stopping threshold. Do not edit `learner.py` from here.
- **HITL (`k06/hitl-control`):** pass `ChurnEngine(hitl=HITL(...))` to hand EXHAUSTED borrowers to a person
  (`hitl.act(..., "handoff", ...)`); overrides are already maker-checker.
- **K17/K18 voice engine:** the webhook join takes the first of `initial_agent_variables.touch_id`, `user_identifier`, `metadata.touch_id`
  that is a ledger touch; the export therefore sends the reserved `instruction_id` in both. Map Sarvam payloads with `sarvam_export.webhook_to_events`; the fields the voice
  engine still misses are listed in `sarvam_export.VOICE_ENGINE_DIFFS`.
- **When `origin/k06/deterministic-states` lands:** declare `churn/fsm.py` there (`STATES`, `TRANSITIONS`).
- **Adding a cohort / rule:** edit the JSON (fields must be in `FEATURE_CATALOGUE`; validation refuses
  unknown fields or operators), add a test, bump `policy_version`.
