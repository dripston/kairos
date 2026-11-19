# backend/ — K06 guardrails + executor on the K03 spine

Python service that decides whether a touch **may** be sent, reserves it atomically, dispatches it once, and cancels queued work when the borrower's situation changes. Rules come from [`../policy`](../policy/README.md); borrowers, loans, EMIs, consent and payments come from the **K03 spine**.

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                            # 229 tests: K01 + K13 cases, K06 ledger/executor/API/HITL, K03 spine + K15 replay
uvicorn kairos_backend.api:app --reload    # http://127.0.0.1:8000/docs
python scripts/bench_ledger.py 50000       # 50k decisions on K03's synthetic dataset
```

K03 / K15 scripts (Varun): `python scripts/spine_report.py`, `scripts/make_dataset.py`, `scripts/load_dataset.py`, `scripts/run_replay.py` — see [`spine/README.md`](spine/README.md).

Environment: `KAIROS_POLICY_DIR` (default `../policy`), `KAIROS_DB` (default `backend/kairos.db`).

## Layout

| Package | Task | Owner | What it does |
|---|---|---|---|
| `spine/` | K03 | Varun | Borrowers, consents, loans, EMIs (obligations), payments, events. Money in paise. Dataset importer + synthetic 50k generator. |
| `replay/` | K15 | Varun | Campaign replay: baseline vs learning policy, dev simulator, 500-chat load test. |
| `kairos_backend/` | K06 | Rehan | Gate, ledger, supervisor, executor, API — reads the spine, never copies it. |

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

## Human in the loop

`kairos_backend/hitl.py` + [`../policy/K06-human-in-the-loop/approval_policy.json`](../policy/K06-human-in-the-loop/README.md) (**DRAFT, pending Ojas**). Everything a person can do to Kairos, deterministic and audited. A person can always make outreach **stricter**; nobody can make it looser than K01.

| What | How it works |
|---|---|
| **Approval queue** | After the Gate says SEND, `reserve()` asks the HITL hook. If an approval rule matches (IVR/voicebot · STANDARD template above 60 DPD · first send after a hardship hold · any manual send) the touch is stored as **`PENDING_APPROVAL`**: it holds its cap slot and budget like `RESERVED` (nothing can overbook), and the Executor never sends it (`begin_dispatch` only moves `RESERVED`). **Approve** re-runs the full Gate atomically (STOP/consent/caps/window/budget/template) → `RESERVED`, or cancels if the world changed; the Executor checks again at send time. **Reject** cancels and releases budget. Unanswered approvals **expire** at the end of the contact window (`tick`). Maker-checker: whoever scheduled a manual send can't approve it. |
| **Borrower actions** | `pause` / `resume` · `hold` until a datetime (payment re-checked when it ends, like a PTP) · `cancel_next` · `handoff` to a queue (ticket via the existing table, idempotent per day; pauses outreach) · `assign` to an agent · `dnc` (= STOP on every channel). Each needs actor + role + **reason** and writes **one** audit row. Holds set K06's `supervisor_hold`, so the Gate enforces them everywhere. Resuming a hold a person didn't place (a K13 abusive/legal/fraud hold) needs a supervisor. |
| **Locked by guardrails** | `override_stop`, `override_consent`, `exceed_cap`, `bypass_quiet_hours`, `bypass_contact_window`, `send_unregistered_template`, `force_send`, `undo_dnc`: refused for **every** role and audited as `HUMAN_ACTION_REFUSED`. A manual send the Gate refuses (cap, window, quiet day, no consent, unregistered template…) is refused and audited the same way. DNC is undone only by `record_opt_in` for **one** channel with the borrower's consent version (supervisor). |
| **Manual scheduler** | `schedule_manual(borrower or list, channel, template, at, …)` goes through the same Gate + atomic reserve as automated sends, plus a dedupe rule: one live send per EMI + channel + day, automated or manual. `preview_manual` shows the rendered template (masked for the role), the Gate verdict and the approval need, without reserving. `cancel_scheduled` / `reschedule` (= cancel + new reservation; if the new slot is refused the original is restored, re-checked). |
| **Case queue** | Open tickets by queue with SLA due / breached; `claim`, `resolve` (resolution code), `reopen` (supervisor). Resolving never lifts a hold unless `lift_hold=true` (supervisor): hardship → ends the hold now (the first send after it still needs approval), callback → clears callback pending, handoff → releases its hold, K13 routed-stop → lifts the supervisor hold. |
| **Roles + masking** | `viewer`, `operator`, `supervisor`, `policy_owner`; permission table in `approval_policy.json`. `mask(record, role)`: viewer sees initials, last 2 phone digits, no amounts or emails; others see last 4 phone digits. Audit rows hold borrower ids only; free-text reasons and handoff summaries are scrubbed of phone numbers and emails before storage. |
| **Audit** | The K06 `audit` table (SQLite, durable) with indexes on borrower, type and actor. `GET /hitl/audit` filters by borrower, actor, type (repeatable), instruction, since/until, human_only. Human rows: `HUMAN_ACTION`, `HUMAN_ACTION_REFUSED`, `APPROVAL_GRANTED/BLOCKED/REJECTED`, `MANUAL_SCHEDULED`, `SCHEDULE_CANCELED/RESCHEDULED`, `CASE_CLAIMED/RESOLVED/REOPENED`, `TEMPLATE_META_SET`; system rows `APPROVAL_EXPIRED`, `HOLD_RELEASED`. |

**Routes** (role in header `X-Kairos-Role`, default `viewer` — **a demo switch, not authentication**; put SSO + server-side roles in front before real use). Write bodies carry `actor`, `reason` and an optional `now` (demo clock).

| Route | Does |
|---|---|
| `GET /hitl/policy` | approval policy + what this role may do |
| `GET /hitl/approvals?status=PENDING\|ALL` · `POST /hitl/approvals/{id}/approve` · `…/reject` | approval queue |
| `GET /hitl/borrowers/{id}` · `POST /hitl/borrowers/{id}/actions` | holds/assignment/DNC + `{action, reason, until?, days?, queue?, summary?, agent?, channel?, consent_version?}` |
| `POST /hitl/manual/preview` · `POST /hitl/manual` · `DELETE /hitl/manual/{id}?actor=&reason=` · `POST /hitl/manual/{id}/reschedule` | manual scheduler |
| `GET /hitl/cases?queue=&status=OPEN\|CLAIMED\|RESOLVED\|ALL` · `POST /hitl/cases/{ticket}/claim\|resolve\|reopen` | case queue |
| `GET /hitl/audit` | audit search |
| `PUT /hitl/templates/{id}` | template intensity (LOW/STANDARD) + body for preview (policy_owner) |
| `POST /hitl/tick?now=` | expire approvals, release manual holds whose date came |

Errors: 403 role not permitted / maker-checker, 409 locked by guardrails or wrong state (`detail.code`), 422 missing reason or bad input, 404 unknown id.

**How to continue.** *Ojas* must confirm in `approval_policy.json`: which sends need approval (the four rules, and the 60-DPD threshold — the Control Room toggle says "60+"), who may approve (today supervisor + policy owner, not operator) and whether operators may DNC, the expiry (end of contact window), maker-checker, the manual dedupe scope, the handoff SLA (4 h) and the resolution codes; then flip `status` and `approved_on`. *Frontend:* the Control Room should poll `GET /hitl/approvals` for the "Needs approval" filter and the Inbox "Approvals" tab, and call approve/reject with the reason from `ReasonDialog`; `GET /hitl/cases` feeds "Needs a human" (Claim → `/claim`, Resolve → `/resolve` with a resolution code and, for supervisors, a "lift hold" checkbox). `CrmAccount` "Borrower actions" map 1:1 to `POST /hitl/borrowers/{id}/actions` (`pause`/`resume`/`hold` with `days: 2`/`cancel_next`/`handoff`/`assign`/`dnc`); the "Locked by guardrails" buttons can call the same route with `override_stop` / `exceed_cap` to show the audited refusal. Queue row cancel/reschedule → `DELETE /hitl/manual/{id}` / `…/reschedule` (works for automated touches too). Add a `supervisor` option to the role switch (store has operator / policy only) and send it as `X-Kairos-Role`. Call `POST /hitl/tick` on each simulated day/hour advance. Global pause stays `POST /control/outbound` (it now also cancels pending approvals). Not built yet: segment (band) pause and the Control Room approval-mode toggles writing back to the policy (they should go through Ojas's file, not the UI).

**Ledger changes for HITL (all additive):** `PENDING_APPROVAL` added to `LIVE` (counts toward caps and budget) · new `QUEUED = (RESERVED, PENDING_APPROVAL)` used by `_cancel_queued`, so STOP / holds / payments / global pause also cancel pending approvals and release their budget · `Instruction.origin` (`auto`/`manual`, default `auto`) · `Ledger.reserve_hook` (default `None`) called inside `reserve()`'s transaction after the Gate allowed the touch; it can only hold the touch as `PENDING_APPROVAL` or veto it. With no hook installed the ledger behaves exactly as before.

## Interpretations (flag if wrong)

- **PTP hold is borrower-wide** — K01 says "no activity on any channel".
- **K13 holds not in K01's order** (`CALLBACK_PENDING`, `SUPERVISOR_HOLD`) run right after `HARDSHIP_HOLD` until Product places them (`gate.EXTRA_RULES_AFTER`).
- **Verified payment cancels that EMI's queued touches immediately**, even during a PTP hold, and ends the PTP hold if it was for that EMI.
- **Daily pacing is a hard limit** (floored to whole paise). Toggle with setting `enforce_daily_pacing`.
- **Weekly caps use a rolling 7 days** (stricter than a calendar week).
- **Any hold cancels the borrower's queued touches** so their budget returns to the pool.
- **A case = a loan** (K01 "accounts"): pool and cure rate count enrolled loans.
- `budget_per_case` and collection targets are K01 REQUIRED_INPUTs — supplied per campaign, never defaulted.
