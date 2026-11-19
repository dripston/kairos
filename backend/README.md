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

## CM-03 WhatsApp servicing (SIMULATED)

Inbound WhatsApp servicing agent: **greet → identify → OTP → intent → resolve or hand off → close**, one
deterministic state machine per conversation, on the same SQLite file as the K03 spine and K06 ledger.
Everything is SIMULATED: synthetic borrowers (`Synthetic Borrower <id>`, `+910…` numbers), a synthetic D7
bank feed, and a WhatsApp adapter in dry-run (nothing leaves the machine).

| File | What |
|---|---|
| `kairos_backend/cm03/engine.py` | `ConversationEngine.handle(sender, text, attachment, button, message_id)`: state machine, disclosure guard, hand-offs, D7 recheck, metrics |
| `kairos_backend/cm03/otp.py` | 6 digits, 5-min expiry, 3 attempts → 30-min lockout (per borrower, across chats) + hand-off; HMAC-SHA256 hash only; bound to conversation + borrower + sender; single use |
| `kairos_backend/cm03/fraud.py` | Deterministic detectors, each with a reason: injection, phishing link, spoofed lender, OTP social engineering, other people's data, impersonation, cross-account, UTR format / reused (normalised key) / other borrower / amount mismatch / not in feed after T+2, doctored screenshot (edit software, modified after capture, date before loan or in future, wrong payee, instructions in image text) |
| `kairos_backend/cm03/nlu.py` | Servicing intents (balance, link, statement, NOC, loan details, waiver, complaint, dispute phrasing, language, menu) on top of K09 `intent.py`; one-edit typo tolerance; reference / amount / OTP / claimed-date extractors |
| `kairos_backend/cm03/tools.py` | The audited tool calls (below) + `DryRunWhatsAppAdapter` + HMAC-signed link tokens |
| `kairos_backend/cm03/copy.py` | English + Hindi copy (K22 tone); `PRE_VERIFY_SAFE` = the only messages allowed before OTP |
| `kairos_backend/cm03/api.py`, `static/console.html` | `/cm03/*` routes and the operator console |
| `kairos_backend/cm03/evaluation.py`, `scripts/run_cm03_eval.py` | Seeded eval + 500-concurrency runs → `reports/cm03_eval.json` (gitignored) |
| `tests/test_cm03_*.py` | Engine flows, red-team attacks (`test_cm03_redteam.py`), HTTP route, small seeded eval (70 tests) |

**Tools** (each one a TOOL_CALL envelope on the CRM timeline with provenance, redacted args, result and ms):
`lookup_account`, `account_summary` (K03 `borrower_summary`), `send_otp` / `verify_otp`, `create_payment_link`,
`get_document`, `check_payment_d7`, `record_payment_claim`, `apply_verified_payment` (K06 `record_payment_verified` →
K03 `apply_payment`, only for a D7-SETTLED credit), `create_ptp` (K06 `apply_exception(PTP_OFFERED)` → K13 PTP hold),
`record_stop` (K06 `record_stop` → every channel opted out), `create_ticket` (K13 routed exception or supervisor ticket),
`send_whatsapp_reply`. Envelope kinds: INBOUND, TOOL_CALL, REPLY, STATE, OUTCOME, HANDOFF, FRAUD_SUSPECT, GUARD_BLOCK, ERROR.

**Rules the engine enforces**
- Before OTP: only `PRE_VERIFY_SAFE` copy (who we are + verification, STOP / wrong-number / refusal acks). Any other
  key is swapped for "please verify" and logged as GUARD_BLOCK. A request made before OTP is parked and answered after.
  An unregistered number gets the same answer whether or not the loan id it names exists (no enumeration).
- Payment claims never mark anything paid. Only a SETTLED row in the D7 feed is applied to the spine; PENDING is
  recorded and re-checked (`POST /cm03/d7/recheck`); the receipt is sent when it settles (E14). Partial payments keep
  the EMI open and say what is left; other loans stay independent (E15). Repeated receipt requests reuse the payment id (E16).
- Every fake → refuse, FRAUD_SUSPECT envelope + audit row, supervisor ticket, hand-off; the conversation stays with
  humans (holding reply only; STOP still honoured).
- STOP from the registered number is honoured without OTP, on all channels (K03 consents + K06 `stopped`).
- LLM: only K09's path for ambiguous free text (gpt-6-luna on redacted text), used only when `OPENAI_API_KEY` is set.
  Without a key, unmatched text gets one clarify menu, then a hand-off.

**Defaults chosen where the brief is silent (flag if wrong)**
- *Resolved* = at least one request fulfilled or a correct terminal outcome (balance shared, link sent, PTP recorded,
  claim verified **or recorded as pending with auto-recheck**, document sent, NOC info, STOP honoured, wrong number
  closed) **and** no hand-off. Hardship, dispute, waiver, callback, fraud, OTP lockout, unknown-after-clarify and PTP
  beyond 7 days are hand-offs: correct, but counted against automation.
- *Seeded fake* = the 13 kinds in `FAKE_MIX` (fake / reused / mismatched / never-arrived UTRs, doctored and
  instruction-carrying screenshots, impersonation, prompt injection, phishing link, OTP scam, spoofed lender,
  cross-account, other borrower's data).
- PTP window = K13 `auto_accept_max_days` (7); beyond it the bot asks once for an earlier date, then refers.
- Any URL that is not ours (`pay.kairos.example`, `docs.kairos.example`) is treated as phishing.
- Unregistered sender naming a loan → hand-off (UNREGISTERED_NUMBER); naming a loan **and** asking for its data → fraud.

### Measured result (SIMULATED, seed 20261120, commit 614d3f4)

`python scripts/run_cm03_eval.py --check-determinism` (1,000 conversations sequentially, then 500 at once through
the engine and 500 at once through `POST /cm03/whatsapp/inbound`; about 50 s on a laptop):

| Bar | Result | |
|---|---|---|
| ≥ 70% resolved without a human | **73.2%** (732 / 1,000) | PASS, thin margin: see below |
| Every seeded fake caught | **127 / 127**, 0 false positives on 873 genuine | PASS |
| Zero disclosure before OTP | **0** replies with account tokens before the VERIFIED event (independent scan) | PASS |
| First reply < 30 s | engine p50 0.6 ms, p95 2.4 ms, max 73 ms; 0 over 30 s | PASS (excludes WhatsApp network time) |
| 500 concurrent conversations | engine: 1,490 msgs in 7.2 s, turn p95 3.5 s, first reply p95 1.7 s; HTTP: 7.8 s, turn p95 3.4 s, first reply p95 1.3 s; 0 errors, 0 cross-talk, outcomes identical to the sequential run | PASS |
| Deterministic given a seed | outcome digest `e839b536ca56e4fc` twice | PASS |

Where the 26.8% goes (all hand-offs, by design): fraud 12.7%, hardship 3.8%, dispute 2.9%, callback 2.8%,
unknown after clarify 2.1%, OTP lockout 1.0%, waiver 0.9%. Resolution is 83.9% of genuine conversations and 99.5% of
the conversations a bot should close; route accuracy (bot / human / fraud as labelled) is 99.6%.

**Honest caveats.** (1) The 73.2% depends on the conversation mix (`LEGIT_MIX` / `FAKE_MIX` in `evaluation.py`,
13% fakes, 14% human-by-design); with the hidden mix unknown, more hardship / dispute / fakes would push it below
70%. (2) 5.9 points come from payment claims that are *recorded with a status reply and auto-rechecked* but not yet
verified (39 pending + 20 without reference); counting only verified payments the rate is 67.3%. (3) Latency is engine
processing (and in-process HTTP); WhatsApp delivery time is not included. (4) The scripted borrowers come from the
same team that wrote the rules; typos are one-edit only, so real free text will be harder. Without an LLM key,
unmatched free text goes to clarify, then to a human.

### Demo

```bash
cd backend
KAIROS_CM03_DEMO_INBOX=1 KAIROS_DB=/tmp/cm03-demo.db uvicorn kairos_backend.api:app --reload
# open http://127.0.0.1:8000/cm03/console  (seeds B-1042 / B-2001 / B-3003; the right panel shows the simulated
# phone's OTP, the state, the verified flag and every envelope)
#   B-1042: "Kal EMI pay kar di thi" -> OTP -> "UTR 512345678901" -> receipt, reminder stopped, 2nd loan still open
#   B-2001: "UTR 612345678902" -> pending; POST /cm03/demo/settle {"reference":"612345678902"} -> receipt
#   "Edited screenshot" / "Injection" / "OTP scam" buttons -> refusal + FRAUD_SUSPECT + ticket
python scripts/run_cm03_eval.py                 # full eval (≈ 50 s)
pytest tests/test_cm03_redteam.py -q            # CM-03 red-team attacks
```

### Context for Claude / how to continue

- Entry point is `ConversationEngine.handle`; read `engine.py`'s docstring for the order of checks. New intents: add
  a rule to `nlu._SERVICING` (or K09 `intent.py`), a handler in `_serve`, copy in `copy.py` (en + hi), an outcome in
  `RESOLVED_OUTCOMES` only if no human is needed, and a scenario in `evaluation.py`.
- Never add an account field to a `PRE_VERIFY_SAFE` message; `test_cm03_redteam.py` and the eval's independent token
  scan will fail if you do.
- Not merged here, wire when available: HITL (`origin/k06/hitl-control`, `hitl.py`) for the ticket queue UI; the K07
  phone-demo WhatsApp adapter (`origin/k07-k08/channel-adapters`) only sends approved templates, so free-text replies
  stay dry-run; `spine.ledger.reference_key` from `origin/redteam/fix-high` (used automatically when present; CM-03
  has the same normalisation in `store.reference_key`); the red-team runner on `origin/redteam/guardrail-suite`
  (these attacks live in `tests/test_cm03_redteam.py` because that package is not on this base).
- Known gaps: media download / OCR / voice-note transcription are simulated (OCR fields arrive on the payload);
  verification is per conversation with a 30-min idle session; one process (per-sender locks are in memory);
  `/cm03/*` has no operator auth (same as the rest of the API).
