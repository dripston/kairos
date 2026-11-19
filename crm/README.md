# Kairos CRM: borrower detail + shared event ingestion (K05 / K14)

One borrower page (synthetic **B-1042**, 42 DPD, two loans) with the K05 two-line
summary (channel evidence, then borrower intent, both with source IDs), the next
action and its reason, both loans, and a chronological timeline showing the four
agent outputs, the supervisor decision and the executor outcome (K14).

A **Replay journey** button pushes a synthetic five-step journey through the single
event-ingestion path.

Requires only Python 3.9+ (stdlib + SQLite). No installs. It lives in `crm/`, beside
`frontend/` (React demo UI), `backend/` (K06 supervisor, ledger, executor) and `policy/`;
every command below runs from `crm/`.

## Run

```bash
cd crm
python3 -m kairos.web            # → http://127.0.0.1:8042/borrowers/B-1042
```

On the page: **Replay journey** resets B-1042 and runs all five steps. **Next step**
runs one step at a time (for the demo). **Reset** re-seeds.

Headless:

```bash
python3 -m kairos.replay         # reset + replay, prints PASS/FAIL per step, summary and timeline
python3 -m kairos.replay --step  # next step only
python3 -m unittest -v           # 26 tests
```

The database is `var/kairos.db` (override with `KAIROS_DB=/path.db` or `--db`). It is
rebuilt automatically when the schema version changes.

## The journey

| # | Input (raw payload → FIXTURE adapter) | What the page shows |
|---|---|---|
| 1 | SMS DLR `DELIVRD` for touch `T-1042-0001` | Provider delivery: SMS delivered, read **UNKNOWN**, no reply |
| 2 | WhatsApp reply “thoda time chahiye” (context → `T-1042-0002`) | Message → intent **NEEDS_TIME** → four agent outputs (K05, K09, K21, K22) → supervisor **WAIT** (policy version, 48 h expiry) |
| 3 | WhatsApp reply “PL100328 ki EMI … pay kar di thi” | Intent **CLAIMS_PAID (Loan A)** → payment claim **VERIFICATION PENDING**; Loan A stays overdue |
| 4 | D7 ledger webhook, settled + LMS-allocated to `PL100328` | Verified payment → supervisor **CANCEL** scoped to Loan A → executor cancels `T-1042-0003`; Loan B and `T-1042-0004` stay open |
| 5 | Same ledger webhook retried (new delivery attempt ID) | Ingestion returns `duplicate`; timeline count unchanged; noted under the timeline |

## Voice: Sarvam IVR (K17) and voicebot (K18)

`kairos/voice/` holds both flows as deterministic state machines (`flows.py`). The same
definitions drive the simulator, the exported agent specs and the prompt audio.

| Flow | What it does |
|---|---|
| `ivr_emi_menu_hi_v1` | Hindi menu: 1 = already paid, 2 = need time, 3 = talk to a person, 9 = repeat. At most 3 prompts. A valid digit yields a structured intent. No-answer, no input or invalid digits end with one outcome and **no** intent (E10). |
| `voicebot_callback_hi_v1` | Consented callback: permission → ask intent (2 tries) → needs-time asks once for a date (E07), paid claim is acknowledged but never confirmed, hardship gets a neutral ack and handoff with a restricted reason (E09), callback, STOP. Declined or unclear calls end with no intent. |

Neither flow ever speaks amounts or loan numbers (there is no identity verification
on voice; tested).

**Executor** (`executor.py`): `touch.planned` → recheck voice consent and supervisor
holds (nudges only) → `touch.blocked` (no dial) **or** `touch.sent` → run the flow →
Sarvam call-completion webhook → Sarvam adapter → `ingest_event` → `call.outcome` →
`borrower.intent` → four agent outputs → supervisor decision. On the page, each voice
button starts a fresh branch from the same saved state (journey replayed), as the demo
matrix asks.

```bash
python3 -m kairos.voice list
python3 -m kairos.voice call voicebot_promise_date --branch
python3 -m kairos.voice export                         # → kairos/voice/agents/*.json (checked by a test)
SARVAM_API_KEY=… python3 -m kairos.voice render-prompts    # Sarvam TTS (bulbul:v3) → var/prompts/*.wav
SARVAM_API_KEY=… python3 -m kairos.voice transcribe clip.wav  # Sarvam STT (saaras:v4) → intent + date
```

`kairos/voice/agents/*.json` are the agent definitions plus the callback contract
(`POST /webhooks/sarvam`, one outcome per `call_id`) to configure in Sarvam. Calls are
**SIMULATED** until the Sarvam voice-agent route is authorized. The TTS/STT client
(`sarvam.py`) follows docs.sarvam.ai but has not been exercised with a key yet.

## Data

The loans are rows **PL100328** and **PL100376** from the team's verified dev fallback,
`PL_Dummy_Collections_Data_500` (tracker → Datasets). Loan ID, EMI, POS, TOS and bounce
date are copied from those rows. The dataset has one loan per borrower and only
0–30 DPD, so the seed:

- joins both rows under synthetic borrower B-1042 (the K03 two-loan spine), and
- advances the demo clock 30 days (dataset DPD 12 → 42).

Both adjustments are stored on each loan (`source`, `source_dpd`) and shown on the page.
Names and mobile numbers are not copied. The official CM-02/CM-03 datasets are
still "file link unverified" in the tracker.

## Design

- **One ingestion path:** `kairos/ingest.py::ingest_event`. Replay, `POST /api/events`
  (normalized envelope) and `POST /webhooks/{sms,whatsapp,ledger,sarvam}` (raw payload
  → adapter) all call it. Follow-on work (intent → agent outputs → supervisor decision →
  executor; claim; CANCEL) is emitted as derived events through the same function, in
  the same transaction.
- **Idempotency:** each envelope carries a business `dedupe_key`, e.g. `ledger:<txn_ref>:SETTLED`,
  `delivery:<provider>:<msg_id>:<status>` or `call:sarvam:<call_id>`. The UNIQUE
  `events.dedupe_key` decides it. A repeat goes to `duplicate_events` with no side
  effects, and `timeline_entries.event_id` is UNIQUE (one row per accepted event).
- **IDs connect events:** a reply's provider context ID resolves to a `touch_id`, which
  fills in `borrower_id` and `campaign_id`. Loans are checked against the borrower. Every
  derived event records its `causation_event_id`.
- **Kept separate:** `provider_deliveries` and `call_outcomes` (provider side),
  `borrower_messages` / `borrower_intents` (what the borrower said and meant),
  `payment_claims` (borrower says paid) and `verified_payments` (ledger only). Only a
  verified payment changes a loan. A late `SENT` never rewinds `DELIVERED`. An IVR
  outcome never invents an intent.
- **Supervisor (K06 / K14):** SEND / WAIT / CANCEL / HANDOFF with reason, policy version,
  expiry, scope loan, and the ID of the agent-outputs event it read.
- **Labels:** every event carries `provenance` (`LIVE` / `REPLAY` / `SIMULATED`), plus
  `fixture_of` when a fixture produced it. The page badges both.

## Fixtures pending teammates' interfaces

Labeled stand-ins live in `kairos/adapters/fixtures.py`. Each produces events badged
`FIXTURE · <name>`. Protocols are in `kairos/adapters/__init__.py`.

| Fixture | Owner / task | Replace with |
|---|---|---|
| `rehan.sms_provider_adapter` | Rehan · K08 | SMS DLR adapter (`ProviderAdapter.normalize`) |
| `rehan.whatsapp_provider_adapter` | Rehan · K07 | Meta WhatsApp inbound adapter |
| `rehan.d7_lms_ledger_adapter` | Rehan · K11 | D7 settlement + LMS allocation adapter |
| `rehan.intent_classifier` | Rehan / Ojas · K10 | Intent classifier (`IntentClassifier.classify`) |
| `fixture.recommendation_agents` | Rehan K09, Ojas K21, Yuval K22 | `RecommendationAgents.recommend` (K05 summary is real) |
| `rehan.supervisor` | Rehan · K06 | `Supervisor.decide`. Policy is `policy-draft-0`, **not** Ojas-approved |
| `sarvam.call_adapter` | Rehan IVR / Yuval K18 | Sarvam call-completion webhook (contract proposed in `kairos/voice/agents`) |

## Not done yet

- **Sarvam live route (K17/K18):** flows, executor, consent gate and ingestion are built
  and tested; placing real calls needs the Sarvam voice-agent MCP authorized, the specs in
  `kairos/voice/agents` loaded there, and the callback contract confirmed.
- **Voicebot NLU** is rule-based Hinglish keyword spotting; swap in Sarvam STT + an LLM
  classifier for live audio.
- **Duplicate dispatch (E03)** and budget reservation depend on Rehan's executor; blocked
  sends are shown for consent and hold gates on voice touches.
- **Wire into the rest of the repo:** (1) replace `rehan.supervisor` (fixture) with the K06
  `kairos_backend.executor.Supervisor` / ledger in `backend/`, which already implements
  scoped cancel on verified payment and the K01/K13 gate; (2) point the React CRM account
  page (`frontend/src/pages/CrmAccount.jsx`, currently `mock.js`) at
  `GET /api/borrowers/B-1042` and the replay / voice endpoints here.
