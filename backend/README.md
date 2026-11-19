# backend/ — K06 guardrails + executor on the K03 spine

Python service that decides whether a touch **may** be sent, reserves it atomically, dispatches it once, and cancels queued work when the borrower's situation changes. Rules come from [`../policy`](../policy/README.md); borrowers, loans, EMIs, consent and payments come from the **K03 spine**.

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                            # 255 tests: K01 + K13 cases, K06 ledger/executor/API, K03 spine + K15 replay, channels
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
| `kairos_backend/channels/` | K07/K08 | Rehan | SIMULATED sms/whatsapp/ivr/voicebot providers, callback normalizer, phone-demo adapters (see [Channels](#channels)). |

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
| `android_sms.py` | `AndroidSmsGatewayProvider`: SMS from a team member's Android phone via the open-source SMS Gateway for Android app ([sms-gate.app](https://sms-gate.app), Apache-2.0). Same rails; dry-run by default. |
| `demo_recipients.py` | Runtime allow-list (`~/.kairos/demo_recipients.json`, outside the repo), merged with `KAIROS_DEMO_ALLOWLIST`. |

**Callback rules** (K01 `reply_attribution`, edge cases E02/E04/E06/E10):
- Delivery, read and link-click are never replies. A click only updates reachability (E06).
- SMS read is always `UNKNOWN` — never inferred from delivery (E04).
- Status only moves forward (`ledger.RANK`); a late `SENT` after `DELIVERED`, or any duplicate, changes nothing (E04). A `FAILED` receipt after the touch is `SENT` stays `SENT` in the ledger (it still counts toward caps) and the envelope says `UNREACHABLE`.
- Inbound text: `STOP`/`UNSUBSCRIBE`/`STOPALL`/`CANCEL`/`END`/`QUIT`/`OPT OUT`/`रोकें`/`बंद`, or text starting with `STOP ` → `record_stop` (all channels, all queued work — E02). Other text → `record_response` (stop for the day).
- Calls: one normalized outcome per instruction; a duplicate completion is ignored (E10). Pickup → `record_call` (stop for the day over 10 s). Only a valid IVR digit (demo menu `1` payment link, `2` callback, `9` STOP) or a voicebot structured intent counts as a captured response. Invalid digit, no input, no-answer, busy and decline are not replies; no intent is invented. Voicebot `PROMISE_TO_PAY` is reported in the envelope (`intent_detail`) but does **not** set a PTP hold — that stays with K13/K06.

### Phone demo (team member's own phone, no number purchase)

Every demo adapter refuses unless **all** of these hold: env flag set, recipient in the allow-list (`KAIROS_DEMO_ALLOWLIST` ∪ `~/.kairos/demo_recipients.json`), text from an approved demo template id, credentials in env (never in code). They default to **dry run**: the exact osascript command / HTTP request is built and kept in `provider.dry_runs` (numbers and token masked), and `send()` returns `REJECTED("DRY_RUN")` so the ledger releases the budget. Logs mask phone numbers to the last 4 digits. The recipient is `touch["to"]`, or the borrower's `phone` in the K03 spine when `build_providers(..., ledger=ledger)`.

**Which SMS provider?** In `phone-demo` mode the `sms` channel is picked by env, first match wins (`channels.select_sms_provider`):
1. `KAIROS_DEMO_ANDROID_SMS=1` → `AndroidSmsGatewayProvider` (your Android phone, section 0)
2. `KAIROS_DEMO_SMS=1` → `MessagesAppSmsProvider` (your Mac + iPhone, section 1)
3. neither → the SIMULATED sms mock (nothing leaves the machine)

**0. Android: send SMS from your own phone**

The free, open-source app *SMS Gateway for Android* ([sms-gate.app](https://sms-gate.app), Apache-2.0) turns your phone into a small SMS server. Kairos asks it to send one approved demo text; your phone sends a normal SMS from your own SIM (your normal SMS charges apply).

1. **Install the app** (Android 5.0 or newer). On the phone, open [github.com/capcom6/android-sms-gateway/releases](https://github.com/capcom6/android-sms-gateway/releases) (also linked as *Download App* on sms-gate.app), download the latest `app-release.apk` and open it. Android will ask to allow installs from your browser: allow it for this install. This is the only source the official docs name (checked 2026-11-19); they don't list Google Play or F-Droid, so avoid copies from other sites.
2. **Grant permissions.** Open the app and allow **SMS** (sending is required). Allowing **Phone** lets you pick a SIM; allowing receiving SMS lets replies come back as webhooks (optional).
3. **Turn on Local Server.** Put the phone on the **same Wi-Fi** as your laptop. In the app's **Home** tab, switch on **Local Server**, then tap **Start Service**. The Local Server card now shows the phone's **local address** (like `192.168.1.23`, port `8080`), a **username** and a **password**. Write them down. (Optional check: open `http://<that address>:8080/docs` on the laptop. A login box means the laptop can reach the phone.)
4. **Set the env vars** in the terminal you'll run Kairos from (values from step 3, never commit them):
   ```bash
   export KAIROS_DEMO_ANDROID_SMS=1
   export SMSGATE_MODE=local
   export SMSGATE_URL=http://192.168.1.23:8080      # the address the app shows, with :8080
   export SMSGATE_USER=...                         # username from the app
   export SMSGATE_PASSWORD=...                     # password from the app
   # optional: export SMSGATE_SIM=2   (dual SIM)    export SMSGATE_TTL_S=3600   (unsent messages expire; 0 = never)
   ```
5. **Add the receiving number to the demo recipients** (only numbers on this list can ever get a demo SMS). Either use the frontend's *Demo recipients* panel / `curl -X POST http://127.0.0.1:8000/demo/recipients -H 'Content-Type: application/json' -d '{"number":"98XXXXXXXX"}'` (saved only on this machine in `~/.kairos/demo_recipients.json`), or `export KAIROS_DEMO_ALLOWLIST=+9198XXXXXXXX`.
6. **Dry run** (nothing is sent; prints the exact request with the number and password masked). From `backend/`:
   ```bash
   python scripts/send_demo_sms.py --to <last 4 digits> --template DEMO_SMS_HELLO
   ```
   `python scripts/send_demo_sms.py --list` shows the approved templates and the masked allow-list.
7. **Real send:** the same command with `--live`. The phone sends the SMS; the command prints `result: ACCEPTED provider_ref=DEMO-CLI-…`. Then ask the phone what happened:
   ```bash
   python scripts/send_demo_sms.py --to <last 4 digits> --template DEMO_SMS_HELLO --live
   python scripts/send_demo_sms.py --status DEMO-CLI-…      # Pending/Processed -> ACCEPTED, Sent, Delivered, Failed
   ```

*Phone and laptop not on the same Wi-Fi?* Use **cloud mode**: in the app switch on **Cloud Server** instead, tap **Start Service** → **Sign Up**; it shows a generated username and password. Set `SMSGATE_MODE=cloud` and those credentials, and leave `SMSGATE_URL` unset (it uses `https://api.sms-gate.app/3rdparty/v1`). Messages then pass through SMSGate's public relay, so the number and text leave your network; prefer local mode for real numbers.

*Troubleshooting:* `SMSGATE_UNREACHABLE` or a timeout → the service is stopped, the phone is asleep or on another network, or the Wi-Fi blocks devices from talking to each other (guest networks often do). `SMSGATE_401` → username/password don't match the app. `SMSGATE_404` on send → update the app (older versions only had `/message`).

*What Kairos sends* (from the SMSGate API spec): `POST {SMSGATE_URL}/messages` (cloud: `https://api.sms-gate.app/3rdparty/v1/messages`), HTTP Basic auth, body `{"id": "<instruction_id>", "textMessage": {"text": "..."}, "phoneNumbers": ["+91..."], "withDeliveryReport": true, "ttl": 3600}`. `202` → `ACCEPTED` (`provider_ref` = the id). The id is the instruction id, so a resend gets `409` (treated as already accepted, never sent twice) and `status()` can look a send up after a timeout (`GET /messages/{id}`).

*Receipts and replies (optional):* the app can POST `sms:sent` / `sms:delivered` / `sms:failed` / `sms:received` webhooks. Pass each JSON body to `provider.ingest_webhook(payload)` (or `raw_body=..., headers=...`) and the events to `apply_all(ledger, ...)`: sent/delivered/failed only update reach (never a reply); an inbound SMS is a reply on the last demo SMS sent to that number, and `STOP` opts the borrower out on every channel. Register one webhook per event, e.g. `curl -u "$SMSGATE_USER:$SMSGATE_PASSWORD" -X POST "$SMSGATE_URL/webhooks" -H 'Content-Type: application/json' -d '{"id":"kairos-delivered","url":"https://<your-https-endpoint>/smsgate","event":"sms:delivered"}'`. The app only calls HTTPS URLs (or `http://127.0.0.1`, e.g. through `adb reverse`); Kairos has no webhook HTTP route yet. To check signatures, copy *Settings → Webhooks → Signing Key* from the app into `SMSGATE_WEBHOOK_SIGNING_KEY`; then unsigned or stale (> 5 min) webhooks are refused. Without webhooks, use `--status`.

*Have an iPhone instead?* The iPhone can't run a gateway app, so the iPhone path goes through your Mac (section 1). On the iPhone: **Settings → Apps → Messages → Text Message Forwarding** (older iOS: Settings → Messages → Text Message Forwarding) and switch on your Mac. Both devices must be signed in to the same Apple Account in Messages. Then on the Mac `export KAIROS_DEMO_SMS=1` (and *not* `KAIROS_DEMO_ANDROID_SMS`, which takes priority) and use the same `send_demo_sms.py` commands. The first live send asks macOS to let your terminal control Messages; allow it in System Settings → Privacy & Security → Automation.

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
