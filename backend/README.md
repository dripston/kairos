# backend/ — K06 Supervisor guardrails + executor

Python service that decides whether a touch **may** be sent, reserves it atomically, dispatches it once, and cancels queued work when the borrower's situation changes. Reads all rules from [`../policy`](../policy/README.md).

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                            # 116 tests: all K01 + K13 cases, ledger, executor, API, K20 email
uvicorn kairos_backend.api:app --reload    # http://127.0.0.1:8000/docs
python scripts/bench_ledger.py 50000       # throughput check (50k decisions)
```

Environment: `KAIROS_POLICY_DIR` (default `../policy`), `KAIROS_DB` (default `backend/kairos.db`).

## How it fits together

```
agent recommendation ──► Supervisor.decide() ──► Ledger.reserve()  ── one write transaction ──┐
                                                   │ facts_for(): counts, holds, budget        │
                                                   │ Gate.evaluate(): K01 rules in policy order │
                                                   └► SEND → touch RESERVED + cost reserved ◄──┘
Executor.dispatch() ──► Ledger.begin_dispatch()  (re-check at send time; else CANCEL + release)
                    ──► Provider.send()          ACCEPTED → SENT · REJECTED → FAILED (release)
                                                 no answer → UNCERTAIN → resolve_uncertain() asks the provider
borrower events (reply, call >10 s, STOP, payment, K13 exceptions) ──► Ledger cancels queued work in scope
```

| Module | Responsibility |
|---|---|
| `policy.py` | Loads K01/K13, lender and official caps; strictest-wins caps, longest gap, calendar (IST). |
| `gate.py` | Pure rule check. Follows `evaluation_order` from policy.json; first hit wins; every decision carries `policy_version`. |
| `exceptions.py` | K13 decision table (needs time, PTP, callback, hardship, routed types). |
| `ledger.py` | SQLite state + **atomic check-and-reserve**, budget pool and daily pacing, cancellations, tickets, audit log, independent cap-breach audit. |
| `executor.py` | `Supervisor` (versioned instruction with reason + expiry) and `Executor` (dispatch once, uncertain-send handling). `FakeProvider` stands in for K07/K08/K17 adapters. |
| `api.py` | FastAPI endpoints for the frontend and other agents. |
| `email_channel.py` | K20 email channel: sandbox-only SMTP adapter, labelled REPLAY provider, delivery/bounce/unsubscribe callbacks → ledger. See [Email channel (K20)](#email-channel-k20). |

## Guarantees and how they're tested

| K06 "done when" | Mechanism | Test |
|---|---|---|
| Parallel workers stay within caps and budget | `BEGIN IMMEDIATE` around facts → Gate → insert; reserved touches count immediately | `test_parallel_workers_*` (run 30× without a flake) |
| Same instruction never dispatched twice | Stable `instruction_id`; RESERVED → DISPATCHING happens once | `test_same_instruction_reserved_once`, `test_dispatch_happens_once` |
| Check uncertain sends before retry | UNCERTAIN keeps its cap slot and cost; `resolve_uncertain` asks the provider, never resends | `test_uncertain_*` |
| STOP / verified payment cancel pending work within scope | STOP → all channels; payment → that obligation only | `test_stop_*`, `test_verified_payment_cancels_only_that_obligation` |
| Reconcile actual charge once | `reconciled` flag | `test_reconcile_exactly_once` |
| Zero cap breaches | Gate at approval + send time; independent `cap_breaches()` recount | every concurrency test + `/cap-breaches` |
| 50k decisions < 10 min | ~54 s for 50,000 full atomic decisions on a laptop | `scripts/bench_ledger.py` |

## Interpretations (flag if wrong)

- **PTP hold is borrower-wide** — K01 says "no activity on any channel".
- **K13 holds not in K01's order** (`CALLBACK_PENDING`, `SUPERVISOR_HOLD`) run right after `HARDSHIP_HOLD` until Product places them (`gate.EXTRA_RULES_AFTER`).
- **Verified payment cancels that obligation's queued touches immediately**, even during a PTP hold (event-driven, independent of rule order).
- **Daily pacing is a hard limit** (brief: stay within daily *and* campaign budget). Toggle with setting `enforce_daily_pacing`.
- **Weekly caps use a rolling 7 days** (stricter than a calendar week).
- **Any hold cancels the borrower's queued touches** so their budget returns to the pool; the Supervisor replans after the hold.
- `budget_per_case_inr` and collection targets are K01 REQUIRED_INPUTs — supplied per campaign, never defaulted.
- Money is compared at 1/100 paisa precision to avoid float drift.

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
