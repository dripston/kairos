# backend/ — K06 guardrails + executor on the K03 spine

Python service that decides whether a touch **may** be sent, reserves it atomically, dispatches it once, and cancels queued work when the borrower's situation changes. Rules come from [`../policy`](../policy/README.md); borrowers, loans, EMIs, consent and payments come from the **K03 spine**.

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                            # K01 + K13 cases, K06 ledger/executor/API/SupervisorFSM/HITL, K03 spine + K15 replay, K09, K17/K18 voice, K26 reports, K07/K08 channels, K22 templates + compliance checklist, K20 email, red-team (open findings = strict xfail)
python scripts/run_redteam.py     # guardrail red-team: PASS/FAIL per attack -> reports/redteam_report.json (see redteam/README.md)
uvicorn kairos_backend.api:app --reload    # http://127.0.0.1:8000/docs
python scripts/bench_ledger.py 50000       # 50k decisions on K03's synthetic dataset
python scripts/report_pack.py              # K26: seeded SIMULATED campaign -> reports/report_pack/*.csv
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
| `redteam/` | — | — | Guardrail red-team suite: 49 adversarial attacks on K01/K13/K06/K03 guardrails, PASS/FAIL report, open findings. [`redteam/README.md`](redteam/README.md) |
| `kairos_backend/channels/` | K07/K08 | Rehan | SIMULATED sms/whatsapp/ivr/voicebot providers, callback normalizer, phone-demo adapters (see [Channels](#channels)). |
| `kairos_backend/templates.py`, `template_compliance.py` | K22 | Yuval | Approved-template selector over `policy/K22-template-catalog/catalog.json` (`sync_to_ledger` feeds the Gate's `TEMPLATE_APPROVED` check) and the 14-rule collections-compliance checklist (see [K22 template selector](#k22-template-selector)). |
| `kairos_backend/email_channel.py` | K20 | Yuval | Email channel: sandbox-only SMTP adapter, labelled REPLAY provider, delivery/bounce/unsubscribe callbacks → ledger (see [Email channel](#email-channel-k20)). |

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

## Channels

`kairos_backend/channels/` gives the Executor a provider for every outbound channel and turns provider callbacks into ledger updates. Email is K20's (`email_channel.py`); until it lands, `email` is a `FakeProvider`.

```python
from kairos_backend.channels import build_providers, apply_all
providers = build_providers("mock", seed=42, profile="realistic")   # or "phone-demo"; default from $KAIROS_CHANNELS
ex = Executor(ledger, providers)
res = ex.dispatch(instruction_id)
envelopes = apply_all(ledger, providers[channel].callbacks(instruction_id))   # replay the provider's callbacks
```

| Module | What it does |
|---|---|
| `mock.py` | `MockChannelProvider(channel, seed, profile)` for sms / whatsapp / ivr / voicebot, `provenance="SIMULATED"`. Deterministic per (seed, instruction_id). Profiles `realistic` (default), `perfect`, `chaos`, or a dict of overrides (`delivery_rate`, `read_rate`, `click_rate`, `reply_rate`, `stop_rate`, `answer_rate`, `busy_rate`, `valid_digit_rate`, `invalid_digit_rate`, `intent_rate`, `decline_rate`, `duplicate_rate`, `late_rate`, `timeout_rate`, `timeout_lost_share`, `reject_rate`). `callbacks(iid)` returns normalized events in arrival order, duplicates and out-of-order arrivals included. |
| `normalize.py` | `apply_callback(ledger, event)` → envelope `{event_type: provider.delivery / borrower.message / call.outcome, dedupe_key, provenance, applied, duplicate, ignored_reason, counts_as_reply, read_status, reachability, outcome, intent, stop, canceled, touch_status, …}`. Idempotent (claims each `dedupe_key` in a `channel_events` table in the same SQLite file). |
| `phone_demo.py` | `MessagesAppSmsProvider`, `WhatsAppCloudTestProvider`, `click_to_chat_url()`. OFF unless env flags are set; dry-run by default. |

**Callback rules** (K01 `reply_attribution`, edge cases E02/E04/E06/E10):
- Delivery, read and link-click are never replies. A click only updates reachability (E06).
- SMS read is always `UNKNOWN` — never inferred from delivery (E04).
- Status only moves forward (`ledger.RANK`); a late `SENT` after `DELIVERED`, or any duplicate, changes nothing (E04). A `FAILED` receipt after the touch is `SENT` stays `SENT` in the ledger (it still counts toward caps) and the envelope says `UNREACHABLE`.
- Inbound text: `STOP`/`UNSUBSCRIBE`/`STOPALL`/`CANCEL`/`END`/`QUIT`/`OPT OUT`/`रोकें`/`बंद`, or text starting with `STOP ` → `record_stop` (all channels, all queued work — E02). Other text → `record_response` (stop for the day).
- Calls: one normalized outcome per instruction; a duplicate completion is ignored (E10). Pickup → `record_call` (stop for the day over 10 s). Only a valid IVR digit (demo menu `1` payment link, `2` callback, `9` STOP) or a voicebot structured intent counts as a captured response. Invalid digit, no input, no-answer, busy and decline are not replies; no intent is invented. Voicebot `PROMISE_TO_PAY` is reported in the envelope (`intent_detail`) but does **not** set a PTP hold — that stays with K13/K06.

### Phone demo (team member's own phone, no number purchase)

Both adapters refuse unless **all** of these hold: env flag set, recipient in `KAIROS_DEMO_ALLOWLIST`, text from an approved demo template id, credentials in env (never in code). They default to **dry run**: the exact osascript command / Graph API request is built and kept in `provider.dry_runs` (numbers and token masked), and `send()` returns `REJECTED("DRY_RUN")` so the ledger releases the budget. Logs mask phone numbers to the last 4 digits. The recipient is `touch["to"]`, or the borrower's `phone` in the K03 spine when `build_providers(..., ledger=ledger)`.

**1. SMS through your Mac + iPhone (macOS only)**
1. iPhone: Settings → Apps → Messages → **Text Message Forwarding** → enable your Mac. Sign in to the same Apple Account in Messages on the Mac. Send yourself one SMS by hand from Messages to confirm it goes green.
2. The first real run asks macOS to allow your terminal to control Messages (System Settings → Privacy & Security → Automation). Accept it there yourself.
3. Env:
   ```bash
   export KAIROS_DEMO_SMS=1
   export KAIROS_DEMO_ALLOWLIST=+9198XXXXXXXX          # your own phone(s), comma-separated E.164
   # optional: export KAIROS_DEMO_SMS_IMESSAGE_FALLBACK=1   # use iMessage only if the SMS account fails
   ```
4. Approved demo templates: `DEMO_SMS_HELLO`, `DEMO_SMS_REMINDER_EN` (`phone_demo.DEMO_SMS_TEMPLATES`). Register the id in the ledger too (`ledger.upsert_template("DEMO_SMS_HELLO", "sms", "en")`) so the gate's TEMPLATE_APPROVED rule passes.
5. No delivery receipts: AppleScript can't see them. A successful send is `SENT`; delivery and read stay `UNKNOWN`.

**2. WhatsApp through Meta's free test number**
1. developers.facebook.com → My Apps → Create app (type Business) → add the **WhatsApp** product. Meta gives you a **test business phone number** and its **Phone number ID** on *WhatsApp → API Setup*.
2. On API Setup, add your phone in the **To** list and confirm it with the code WhatsApp sends you (the test number can only message verified recipients).
3. Generate an access token (the API Setup temporary token expires in about a day; for longer demos create a System User token in Business Settings).
4. Template: every new WhatsApp Business account has the pre-approved `hello_world` (`en_US`) template, mapped to touch template id `DEMO_WA_HELLO`. Add your own approved templates to `DEMO_WA_TEMPLATES` (`{"name", "language", "body_params": [...]}`), and register the id in the ledger (`upsert_template("DEMO_WA_HELLO", "whatsapp", "en")`).
5. Env:
   ```bash
   export KAIROS_DEMO_WHATSAPP=1
   export KAIROS_DEMO_ALLOWLIST=+9198XXXXXXXX
   export WA_TOKEN=...                 # never commit it
   export WA_PHONE_NUMBER_ID=...       # numeric id from API Setup
   # optional: export WA_GRAPH_VERSION=v23.0
   ```
6. Request sent: `POST https://graph.facebook.com/{WA_GRAPH_VERSION}/{WA_PHONE_NUMBER_ID}/messages`, `Authorization: Bearer $WA_TOKEN`, body `{"messaging_product":"whatsapp","recipient_type":"individual","to":"<digits>","type":"template","template":{"name":"hello_world","language":{"code":"en_US"}},"biz_opaque_callback_data":"<instruction_id>"}`. A 200 with `messages[0].id` (a `wamid`) is `SENT`. Delivery/read arrive only by webhook: pass the webhook JSON to `provider.ingest_webhook(payload)` and the events to `apply_all(ledger, ...)`.
7. Zero-setup fallback: `click_to_chat_url("+9198XXXXXXXX", text)` → `https://wa.me/9198XXXXXXXX?text=...`; a human opens it and taps Send (nothing is sent automatically, nothing is recorded as sent).

**3. One dry run, then one real send**
```python
from kairos_backend.channels import build_providers
p = build_providers("phone-demo")                       # dry_run=True
t = {"instruction_id": "DEMO-1", "borrower_id": "B1", "template_id": "DEMO_SMS_HELLO", "to": "+9198XXXXXXXX"}
print(p["sms"].send(t), p["sms"].dry_runs[-1])          # REJECTED DRY_RUN + the masked osascript command
p = build_providers("phone-demo", dry_run=False)        # only now does anything leave the machine
print(p["sms"].send(t))                                 # ACCEPTED -> check your phone
print(p["whatsapp"].send({**t, "template_id": "DEMO_WA_HELLO", "instruction_id": "DEMO-2"}))
```
Through the full stack, pass `ledger=ledger` so the recipient comes from the spine, and put the demo borrower's phone = your allow-listed phone.

### How to continue (K07/K08 real adapters — Rehan)

The mocks fix the contract the real adapters must meet: implement `send(touch) -> ProviderResult` (`ACCEPTED` with `provider_ref` and actual `cost_paise`, `REJECTED` with a reason, or raise / `UNKNOWN` on timeout) and `status(instruction_id, provider_ref)` that really asks the provider (the Executor calls it before any retry). Translate each provider webhook into the normalized event dict (`channel, instruction_id, provider_ref, status, at|occurred_at, dtmf, duration_s, text, intent, provenance="LIVE", event_id`) and call `apply_callback` — dedupe, ordering, STOP and reply attribution are already handled and tested there. Carry the `instruction_id` through the provider (WhatsApp `biz_opaque_callback_data`, SMS gateway custom ref / DLT `msg_id` mapping) so callbacks need no lookup. For WhatsApp, `WhatsAppCloudTestProvider` already builds the production request shape; swapping the test number for a real one is an env change plus webhook signature verification (`X-Hub-Signature-256`) on an HTTPS endpoint. For SMS in India, a DLT-registered sender/template with a gateway (e.g. K08's choice) replaces the Messages app adapter; keep `read_status` UNKNOWN. Open items: wire `apply_callback` into an API webhook route, map voicebot `PROMISE_TO_PAY` into K13's PTP flow, and replace the demo IVR menu with K17's.

## K22 template selector

```python
from kairos_backend.templates import TemplateCatalog
cat = TemplateCatalog.load()                     # policy/K22-template-catalog/catalog.json (KAIROS_POLICY_DIR aware)
cat.sync_to_ledger(ledger)                       # once at startup: Gate checks the same catalog
c = cat.select("whatsapp", "hi", None, "STANDARD", policy.band_for(dpd), intent="NEEDS_TIME")
c.decision, c.template_id, c.reason, c.catalog_version   # ("SEND", "WA_ASKDATE_HI_01", "DE_ESCALATED", "0.2.0-placeholder")
cat.select("sms", "en", "REMINDER", "STANDARD", "B2", tier=4).template_id   # "SMS_FINAL_EN_01" (T4 is B2+ only)
cat.tier_ladder("whatsapp", "hi", "B2")         # {1: "WA_REMIND_HI_SOFT_02", 2: "WA_LATEFEE_HI_01", 3: "WA_PAYLINK_HI_01", 4: "WA_FINAL_HI_01"}
cat.select_for_channels(["whatsapp", "ivr"], "hi", "REMINDER", {"whatsapp": "LOW", "ivr": "WAIT"}, "B2")  # WAIT is per channel
```

Approved only. The language must be a K01 language and match exactly (no fallback). Intensity `WAIT` → WAIT. `LOW` never escalates, `STANDARD` may de-escalate to `LOW`. The band must be listed on the template. REMINDER templates carry a CN tone tier (T1/T2 LOW, T3/T4 STANDARD). Without `tier` the least intense fitting tier wins. At PRE_DUE a REMINDER is served by `PRE_DUE_REMINDER`. `PAYMENT_RECEIVED` needs a verified payment. Ties go to (tier, lowest id). Every template carries an `approval` block from the compliance checklist; `approved` must agree with it. Unknown channel/purpose/intensity/band ids raise `ValueError`, while a genuine "nothing fits" returns WAIT with a reason. **The catalog is a synthetic PLACEHOLDER until the official D13 file arrives.** See [`policy/K22-template-catalog/README.md`](../policy/K22-template-catalog/README.md) for the swap steps. Tests: `pytest tests/test_templates.py tests/test_template_compliance.py`.

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
- **K22: an unsupported language is a WAIT, not a fallback.** K01 says "no suitable template → WAIT". A Marathi speaker gets WAIT, not an English or Hindi template.
- **K22: K13 routed-stop intents** (`ABUSIVE`, `LEGAL_THREAT`, `FRAUD_CLAIM`) and `STOP` map to no template (WAIT `INTENT_NOT_AUTOMATED`) instead of the default REMINDER. `PAYMENT_VERIFIED` maps to `PAYMENT_RECEIVED`. A borrower's unverified `PAID` claim maps to no template (WAIT `PAYMENT_NOT_VERIFIED`): no reminder and no receipt until D7/LMS confirms.
- **K22: `sync_to_ledger` withdraws ledger templates that are not in the catalog** (`withdraw_missing=True`), so placeholder ids stop passing the Gate after the D13 swap.

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

1,500 borrowers × 14 days, WhatsApp drift from day 7, budget pool matched to CN10's unconstrained
spend, K15 caps on every arm, K09 ranking by posterior mean (`--explore` adds Thompson draws; on the
tune seeds it gave +8.1%, +10.6%, −12.4%, +2.9%, so exploration stays in SANDBOX/SIMULATOR learning runs).

**Replay default (k09/optimize):** posterior mean + borrower context, plus two Kairos-side options
(`replay.k09.KairosOptions`), both built only from the ledger:

* `decay=0.7` discounts arm evidence by 0.7 per day since the touch was executed, so the band ×
  channel × slot posterior follows the E17 WhatsApp drift in a few days instead of staying anchored to
  week 1.
* `value_floor_rel=0.5` is a marginal-value spending rule. After 200 finalized touches, a candidate
  whose borrower-adjusted expected replies per rupee is below 0.5 × the campaign's realized replies
  per rupee so far WAITs. K01 pacing carries the unspent money forward. It spends 90–95% of the pool
  (93–100% of what CN10 itself spends) instead of about 100%.

`--legacy-k09` runs the previous default. `--kairos-opts '{...}'` overrides single options.

**Protocol.** Options were tuned on seeds 7, 42, 99 and 2026 and validated on held-out seeds 11, 123
and 555. The final configuration was then run once on unseen seeds 31337, 8080 and 4242, next to the
previous default. The baseline, budget matching, caps and simulator are unchanged: the CN10 columns
are identical in both runs. Change = K09 replies/₹1,000 vs CN10 replies/₹1,000.

| Set | Seed | CN10 replies/₹1k | Previous default | **New default** | Replies CN10 / prev / new | Verified payments CN10 / prev / new | Spend ₹ CN10 / prev / new | K01 breaches / K15 violations |
|---|---|---|---|---|---|---|---|---|
| tune | 7 | 56.18 | 78.30 (+39.4%) | **89.21 (+58.8%)** | 163 / 236 / 242 | 35 / 60 / 59 | 2,901 / 3,014 / 2,713 | 0 / 0 |
| tune | 42 | 58.79 | 74.54 (+26.8%) | **86.50 (+47.1%)** | 169 / 228 / 242 | 53 / 62 / 62 | 2,875 / 3,059 / 2,798 | 0 / 0 |
| tune | 99 | 65.27 | 77.85 (+19.3%) | **88.03 (+34.9%)** | 191 / 237 / 249 | 45 / 62 / 65 | 2,926 / 3,044 / 2,829 | 0 / 0 |
| tune | 2026 | 64.87 | 87.91 (+35.5%) | **92.72 (+42.9%)** | 186 / 265 / 265 | 53 / 69 / 69 | 2,867 / 3,014 / 2,858 | 0 / 0 |
| held-out | 11 | 55.17 | 66.06 (+19.7%) | **82.12 (+48.8%)** | 163 / 207 / 231 | 39 / 48 / 56 | 2,954 / 3,134 / 2,813 | 0 / 0 |
| held-out | 123 | 67.38 | 89.24 (+32.4%) | **97.18 (+44.2%)** | 197 / 269 / 276 | 61 / 72 / 76 | 2,924 / 3,014 / 2,840 | 0 / 0 |
| held-out | 555 | 66.42 | 76.95 (+15.9%) | **86.67 (+30.5%)** | 192 / 232 / 245 | 48 / 65 / 72 | 2,891 / 3,015 / 2,827 | 0 / 0 |
| final | 31337 | 60.93 | 75.67 (+24.2%) | **90.10 (+47.9%)** | 178 / 227 / 245 | 50 / 52 / 57 | 2,921 / 3,000 / 2,719 | 0 / 0 |
| final | 8080 | 60.95 | 89.69 (+47.2%) | **98.23 (+61.2%)** | 179 / 273 / 276 | 41 / 67 / 65 | 2,937 / 3,044 / 2,810 | 0 / 0 |
| final | 4242 | 61.95 | 86.18 (+39.1%) | **96.67 (+56.0%)** | 182 / 265 / 282 | 43 / 76 / 81 | 2,938 / 3,075 / 2,917 | 0 / 0 |

Mean / minimum change vs CN10, previous → new default: tune +30.2% / +19.3% → +45.9% / +34.9%;
held-out +22.7% / +15.9% → +41.2% / +30.5%; final +36.8% / +24.2% → +55.0% / +47.9%. On every
seed the new default gets at least as many replies as the previous one and more than CN10. Verified
payments are higher than CN10 on every seed, but on seeds 7 and 8080 they are slightly lower than
the previous default (60 → 59 and 67 → 65). The new default spends 5–10% less than the previous
default. The ratio still rises when spend is held fixed: `decay` alone uses the whole pool and
reaches +36.1% / +25.3% on the tune seeds. STOP opt-outs are higher than CN10 for both K09 defaults
(e.g. final seeds CN10 10 / 18 / 18, previous 22 / 25 / 23, new 22 / 30 / 27). The replies/₹ bar
does not count this cost.

**What was tried.** Each option was run alone on the tune seeds, change vs CN10 in replies/₹. Spend is
about the same as the previous default unless a spend figure is given:

| Option (alone unless noted) | 7 | 42 | 99 | 2026 | Mean / min | Note |
|---|---|---|---|---|---|---|
| previous default | +39.4 | +26.8 | +19.3 | +35.5 | +30.2 / +19.3 | |
| `decay` 0.9 | +44.1 | +31.8 | +22.3 | +37.6 | +34.0 / +22.3 | |
| `decay` 0.8 | +42.3 | +32.9 | +22.8 | +37.6 | +33.9 / +22.8 | |
| `decay` 0.7 | +44.1 | +39.6 | +25.3 | +35.5 | +36.1 / +25.3 | held-out +29.1 / +15.9 |
| `decay` 0.6 | +46.5 | +37.2 | +31.1 | +45.1 | +40.0 / +31.1 | combined with the floor it did worse than 0.7 (below) |
| `recency: gap` (finer fatigue feature) | +41.7 | +28.4 | +22.8 | +38.1 | +32.8 / +22.8 | adds nothing on top of decay 0.7 (+36.9 / +25.3) |
| `affinity_strength` 10 (borrower × channel) | +40.5 | +27.9 | +19.8 | – | – | within noise; stopped after 3 seeds |
| `hier_strength` 50 (slots pooled across bands) | +34.6 | +20.7 | +18.8 | – | – | worse on all 3; stopped |
| `payment_lambda` 0.1 (demo propensity rule) | – | +38.4 | +0.2 | – | – | unstable; stopped |
| `min_reply_prob` 0.02 (lets email in) | – | +29.6 | +8.7 | – | – | unstable; stopped |
| `min_replies_per_inr` 0.15 (absolute floor) | – | +84.8 | +66.5 | – | – | spends 65% of CN10, payments 51 / 53 (below previous default); rejected |
| `value_floor_rel` 0.5 | +70.8 | +53.1 | +33.6 | +51.2 | +52.2 / +33.6 | spends 83–94% of CN10; 42: replies 216 vs 228 |
| `value_floor_rel` 0.75 / 1.0 | – | +58.5 / +66.2 | +37.2 / +46.1 | +61.2 / – | – | spend falls further (74–91% of CN10) |
| **decay 0.7 + value_floor_rel 0.5** | +58.8 | +47.1 | +34.9 | +42.9 | **+45.9 / +34.9** | chosen; held-out +41.2 / +30.5 |
| decay 0.7 + value_floor_rel 0.75 | +56.2 | +48.3 | +33.7 | +43.7 | +45.5 / +33.7 | held-out +41.1 / +30.5 |
| decay 0.6 + value_floor_rel 0.5 | +53.4 | +40.5 | +34.0 | +46.5 | +43.6 / +34.0 | held-out +40.5 / +25.1 |

Selection rule, set on the tune seeds: best tune mean and minimum replies/₹, with verified payments
and replies at or above CN10 on every tune seed. The held-out seeds only validated candidates. The
two floor levels with decay 0.7 tied there, and 0.5 was kept for its better tune mean. Absolute floors raise replies/₹ mainly by
not spending: about a third of the pool goes unspent and payments fall. So they stay behind a flag.

Reproduce one seed (new default, then the previous default):

```bash
python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --baseline both
python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --legacy-k09
```

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

## Reports (K26)

`kairos_backend/reports.py` builds the lender's five operational sheets (column names exactly as in
"HDFC Reports.xlsx") and the CM-02 daily per-segment report ("spend, response and learning per segment
each day"). Read-only over the one SQLite file; every output is labelled **SIMULATED**.

| Sheet | Columns | One row per |
|---|---|---|
| AICI | Customer, Channel, Attempted Intensity, Connected Intensity | customer × channel |
| Data Dump | Customer, Channel, Date, Time, Disposition | disposition event (IST) |
| Trails | Customer, Channel, Disposition | customer × channel: `Sent > Delivered > Responded` |
| FTD | Customer, Channel, Latest Best Disposition | customer × channel |
| Hourly Report | Customer, Time, Channel | attempt, `Time` = IST hour bucket `2026-11-20 10:00-11:00` |

**Disposition = ordinal (CreditNirvana PRD).** Messages: No data < Sent < Delivered < Read < Clicked <
Responded. Calls (ivr, voicebot): Attempted < No answer < Busy < Connected. A touch's disposition is the
highest label any source reports; a lower or older status never overwrites it (as `ledger.RANK`, E04).
Payments and STOP are separate facts, never dispositions. The full table is `reports.DISPOSITION_MAP`:

| Source | Value | Message | Call |
|---|---|---|---|
| `touches.status` | RESERVED / CANCELED | — (not an attempt) | — |
| | DISPATCHING / UNCERTAIN | No data | No data |
| | FAILED | Failed (ranks with No data) | Failed |
| | SENT | Sent | Attempted |
| | DELIVERED (ledger folds READ in) | Delivered | Attempted (answered ≠ connected) |
| spine `events` | touch_sent | Sent | Attempted |
| | reply_received (not a STOP keyword) | Responded | Connected |
| | payment_received / payment_reversed / consent_changed | — (facts) | — |
| `audit` CALL_RESULT | answered_seconds > K01 threshold (10 s) / ≤ | — | Connected / No answer |
| `channel_events` (K07/K08, optional) | ACCEPTED, SENT / DELIVERED / READ / CLICKED / REPLY | Sent / Delivered / Read (never SMS) / Clicked / Responded | Attempted / Attempted / — / — / Connected |
| | ANSWERED, COMPLETED / NO_ANSWER / BUSY, DECLINED / FAILED / UNKNOWN | — | Connected if duration > 10 s else No answer / No answer / Busy / Failed / No data |

**Daily per-segment report** — `daily_segment_report(ledger, campaign_id, day)`: per DPD band × channel
and per risk cohort (C1–C5 from `loans.risk_tag`, when present) × channel: attempts, reach, meaningful
replies, responders, spend reserved / actual / committed (paise and ₹), unreconciled sends, replies per
₹1,000 committed, cap breaches. Verified payments and reversals are separate (per band / cohort, never per
channel). If K09's tables exist (`learner_arms`, `decision_scores`, `learner_context`) a `learning`
section adds posterior mean + n per arm and the day's decision share per arm; absent tables never fail.

API (additive): `GET /reports/{aici|data-dump|trails|ftd|hourly}?start=&end=&format=json|csv&campaign_id=&hash_ids=`
and `GET /reports/daily-segment?campaign_id=&day=&format=json|csv`. Python: `reports.report_pack(...)`,
`reports.export_csv(rows, path)`, `reports.export_pack({name: rows}, folder)`.

Interpretations (flag if wrong):
- **Attempted Intensity** = touches dispatched to a provider in the period (DISPATCHING, UNCERTAIN, SENT,
  DELIVERED, FAILED; plus spine-only `touch_sent`). A provider rejection is still an attempt; a queued or
  canceled reservation is not. **Connected Intensity** = those whose best disposition reached the borrower
  (Delivered/Read/Clicked/Responded; Connected for calls), judged on every outcome known now.
- **Period**: a date = the whole IST day, end date inclusive; a datetime = exact instant, `[start, end)`.
- **Replies / calls are attributed** to the borrower's latest attempted touch on that channel at or before
  them. Under a `campaign_id` filter, events that cannot be tied to that campaign's touches are dropped.
- **The ledger keeps the slot time, not receipt times**: Sent/Delivered rows show the scheduled time unless
  `channel_events` carries the receipt time.
- **Short pickup** (≤ 10 s) = No answer; **DECLINED** = Busy; a reply on a call channel = Connected.
- **Band in the daily report** = DPD band at 00:00 IST that day (that day's payments rolled back), so a
  payment never moves its own day's activity to CLEARED. Verified payments = paid that day and not reversed.
- **Customer = borrower id** only. `hash_ids=True` gives `CUST-` + HMAC-SHA256(salt, id); set
  `KAIROS_REPORT_SALT` per external share.

**How to continue.** When the K07/K08 adapters merge, `channel_events` is picked up automatically
(Read/Clicked/Busy and receipt times appear without code changes); when K09 merges, the learning section
fills from its tables — adjust `_learning()` if `learner_context` gets a documented schema (today only its
row count and columns are reported). If the lender confirms a different reading of "Attempted/Connected
Intensity" or wants "No data" rows for enrolled-but-untouched customers, change `aici()` / `ftd()` and the
matching tests in `tests/test_reports.py`. For a real lender share, send the `export_pack` folder with
`hash_ids=True` and a fresh salt; generated CSVs live in `backend/reports/` (gitignored).

## Email channel (K20)

`kairos_backend/email_channel.py` adds email as a channel behind the same Gate and Executor. It does not send live borrower mail.

| Piece | What it does |
|---|---|
| `ReplayEmailProvider` (default) | Deterministic and labelled `provenance="REPLAY"`. It accepts the send, then scripts the callback per instruction id, per recipient, or by a tag at the start of the local part (`hard_bounce.x@example.test`): `delivered`, `soft_bounce`, `hard_bounce`, `unsubscribe` (delivered first, then unsubscribe). `rejected` and `timeout` test the send-time paths. `drain()` returns the scripted callbacks. |
| `SmtpSandboxProvider` | Built only when `KAIROS_SMTP_HOST`, `KAIROS_SMTP_PORT`, `KAIROS_SMTP_USER`, `KAIROS_SMTP_PASSWORD` and `KAIROS_SMTP_FROM` are set **and** `KAIROS_SMTP_SANDBOX=1`. Before connecting, it refuses any recipient outside `KAIROS_SMTP_ALLOWED_DOMAINS` (comma list, default `example.test`, subdomains allowed). It builds an `EmailMessage` with `Message-ID <instruction_id@kairos.sandbox>` and `X-Kairos-Environment: SANDBOX`. SMTP 250 becomes ACCEPTED. A refusal, a failed login or a rejected DATA becomes REJECTED. A network error becomes UNKNOWN, never "delivered". Optional `KAIROS_SMTP_STARTTLS` defaults to 1, or 0 on port 465, which uses SMTP_SSL. |
| `email_provider_from_env(resolve_address)` | Returns the SMTP provider when it is fully configured, otherwise REPLAY. `resolve_address` is a `borrower_id → address` callable or mapping. |
| `EmailEvents(ledger).apply(EmailEvent)` | Applies one callback to the ledger and returns a CRM envelope (`event_type`, `event_id`, `occurred_at`, `borrower_id`, `instruction_id`, `channel`, `provenance`, `policy_version`, `payload`, `applied`, `duplicate`). |
| `email_eligible(ledger, borrower_id, address=None)` | Returns `(bool, reason)`. Reasons, in check order: `STOP_OPT_OUT`, `EMAIL_UNSUBSCRIBED`, `EMAIL_HARD_BOUNCE`, `NO_CONSENT`, `EMAIL_ADDRESS_SUPPRESSED`, `ELIGIBLE`. |
| `restore_email_consent(ledger, borrower_id, address, consent_version, at)` | Per-channel email re-entry with a recorded consent version. It refuses an address that hard-bounced for this borrower, and it refuses after STOP (use `Ledger.opt_in` for that). |

Callback semantics (E19). Each callback that is applied writes exactly one audit row (`EMAIL_DELIVERED`, `EMAIL_SOFT_BOUNCE`, `EMAIL_HARD_BOUNCE` or `EMAIL_UNSUBSCRIBE`). A repeated callback writes nothing and returns the original envelope with `duplicate: true`. The dedupe key is the provider's `event_id`, or `kind:instruction_id|borrower_id:address_hash` when there is no `event_id`, so real webhooks should always pass `event_id`. A callback never counts as a reply, so the envelope carries `counts_as_reply: false` and `reward: null`.

| Callback | Envelope `event_type` | Ledger effect |
|---|---|---|
| `delivered` | `provider.delivery` | The touch moves to DELIVERED, under the same never-downgrade rule as `record_provider_status`. It counts as reach only. There is no stop-for-day and no reply reward, because K01 `reply_attribution.does_not_count_as_reply` includes "email delivery". |
| `soft_bounce` | `provider.delivery` | Audit and envelope only. The touch keeps its status and is not marked FAILED. FAILED would release the cap slot and budget for a message the relay may still deliver on retry, and the ledger never downgrades SENT anyway. |
| `hard_bounce` | `provider.delivery` | Inserts `email_suppression(borrower_id, address_hash, 'HARD_BOUNCE', at)`. Only an HMAC-SHA256 of the normalized address is stored, keyed by `KAIROS_EMAIL_HASH_KEY`. Email consent is set to false with `consent_version.email = "hard_bounce@<iso>"`, unless email was already withdrawn, in which case the earlier reason is kept. The borrower's queued EMAIL touches are canceled and their budget released. The bounced touch itself stays SENT. |
| `unsubscribe` (scope `email`, the default) | `consent.changed` | Email consent is set to false with `consent_version.email = "unsubscribed@<iso>"`. Queued EMAIL touches are canceled. SMS, WhatsApp and IVR are untouched. |
| `unsubscribe` (scope `all`) | `consent.changed` | K01 STOP: `opted_out=1` and every queued touch is canceled. |

After a hard bounce or an unsubscribe, the Gate's existing CONSENT rule answers `WAIT / NO_CONSENT` for email, while other channels stay eligible. The Supervisor replans at the next allowed touch. To refuse a blocked address at send time as well (for example, when the CRM still returns it), pass `is_suppressed=suppression_check(ledger)` to either provider.

**Ledger change:** none to `ledger.py`. The module adds two tables (`email_suppression` and `email_events`) to the ledger's SQLite file with `CREATE TABLE IF NOT EXISTS`. It runs inside `ledger.tx()` and reuses the ledger's `_audit` and `_cancel_queued` helpers.

Interpretations (flag if wrong):

- **A List-Unsubscribe is email-scoped.** It is not treated as a K01 STOP. A borrower who writes STOP still goes through `Ledger.record_stop`, or through `unsubscribe` with `scope="all"`.
- **A hard bounce turns off the email consent flag.** That flag is the switch the Gate reads. The reason is kept in `consent_version` and in the suppression table, and a new address can re-enter through `restore_email_consent`.
- **A bounce does not change the touch's status.** That includes UNCERTAIN: `Executor.resolve_uncertain` still asks the provider.
- **SMTP status lookups come from memory.** `status()` returns ACCEPTED when the relay said 250. It returns NOT_FOUND when the connection never got past login, so `resolve_uncertain` releases the slot. In every other case it returns UNKNOWN and the touch stays UNCERTAIN. That status memory is held in the process and is lost on restart, which errs toward keeping the touch UNCERTAIN.

### How to continue

Real sandbox delivery needs approved sandbox SMTP credentials: `KAIROS_SMTP_HOST`, `KAIROS_SMTP_PORT`, `KAIROS_SMTP_USER`, `KAIROS_SMTP_PASSWORD`, `KAIROS_SMTP_FROM`, `KAIROS_SMTP_SANDBOX=1`, and, if the sandbox inbox domain is not `example.test`, `KAIROS_SMTP_ALLOWED_DOMAINS`. Also set `KAIROS_EMAIL_HASH_KEY` in any shared environment. With those set, `email_provider_from_env` switches sends to the relay. Delivery, bounce and unsubscribe callbacks stay REPLAY until two things exist: the sandbox's webhook or DSN feed, and a List-Unsubscribe endpoint. Each one only needs to build an `EmailEvent` with the provider's `event_id` and `provenance="SANDBOX_SMTP"` and call `EmailEvents.apply`. `api.py` still wires every channel to `FakeProvider`. Hooking email into the API (provider plus a callback endpoint) and supplying a `resolve_address` from the borrower master are the next steps. Address lookup is deliberately injected, because the ledger never stores raw addresses.
