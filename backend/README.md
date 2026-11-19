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
| `kairos_backend/` | K09 | Rehan / Varun | `contracts` (shared types), `learner` (Beta–Bernoulli band × channel × slot), `outcomes` (24 h reply window → one outcome per touch), `borrower_context` (engagement × recency multipliers), `intent` (rules + gpt-6-luna), `intent_stack` (rules → small model → local LLM → cloud LLM, see [Intent stack](#intent-stack-rules--small-model--local-llm--cloud-llm)), `propensity` (demo). |
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


## Intent stack (rules → small model → local LLM → cloud LLM)

`kairos_backend/intent_stack.py` classifies free text from borrower replies (SMS / WhatsApp / email),
CM-03 WhatsApp servicing turns and free-text voice-call summaries into 17 labels:
`PAY_NOW, PTP (+date), NEEDS_TIME, ALREADY_PAID, CALLBACK, HARDSHIP, DISPUTE, STOP, WRONG_NUMBER, HUMAN,
BALANCE_QUERY, LINK_REQUEST, STATEMENT_REQUEST, LANGUAGE_CHANGE, ABUSIVE, GREETING (greetings / small talk), UNKNOWN`.
It **only classifies**. What a label does stays with Kairos code (K13 `exceptions.handle`,
`Ledger.record_stop`, the CM-03 flow); no model ever picks an action.

| Tier | What | Runs when | Deterministic because |
|---|---|---|---|
| 1 `rules` | Regexes in English, Devanagari Hindi and Hinglish after typo / transliteration canonicalisation (`nhi→nahi`, `pymnt→payment`, `pleeease→please`, Devanagari digits). Dates: `kal`, `parso`, `25 tareekh`, `next Monday`, `कल`, `२५ तारीख`, `2 din me`, `month end`; hints `after_salary`, `after_festival`, `next_week`, `next_month`; amount type FULL / PARTIAL. | Always first | Pure functions |
| — safety | STOP (any language, plus legacy `is_stop`), HARDSHIP incl. **self-harm** (`needs_human`, flag `self_harm`), ABUSIVE. Precedence STOP > self-harm > hardship > abuse. | Before anything else | A safety hit returns at once: **no lower tier runs, so none can override it** |
| 2 `small_model` | Multinomial Naive Bayes, word 1–2-grams + char 2–4-grams, pure Python. Weights `kairos_backend/data/intent_nb_v1.json` (183 KB: version, training seed 1729, calibration seed, temperature, sha). Temperature fitted on *held-out templates* (4 template folds) so it is not over-confident on new phrasing. When rules found several candidates, the model only picks **among them**. | No rule answered with ≥ 0.85 | Seeded corpus, fixed weights |
| 3 `local_llm` | Ollama `/api/chat`, `temperature 0`, fixed `seed`, `top_k 1`, `format` = strict JSON schema `{label, confidence}`, 4 s timeout, input with every digit, phone, e-mail, id and name masked (`redact_for_llm`). Schema violation / timeout / HTTP error → falls through. | Tier 2's top real-label probability < 0.70, and the server + model answer `GET /api/tags` | temperature 0 + seed + cache |
| 4 `cloud_llm` | The existing `intent.LunaClient` (`gpt-6-luna`, reasoning `none`, strict JSON schema, no tools, `store: false`) on the same masked text. OTHER or `ambiguous: true` → not accepted. | Tiers 1–3 still unsure **and** `OPENAI_API_KEY` set | Strict schema + cache |
| 5 `abstain` | `UNKNOWN`, `needs_human: true`, evidence lists what each tier said. | Every tier unsure | Never a guess |

Every result is `{label, confidence, tier_used, evidence (rule ids / model version + top features /
LLM output), entities (date, date_explicit, date_hint, amount_type), needs_human, safety_flags}` and
is cached by `sha256(normalised text | reference day | stack version)`, so the same input gives the
same output. A safety label that only a model (not a rule) produced is still returned but always
`needs_human: true` (`evidence.safety_unconfirmed_by_rules`).

### Evaluation (`scripts/eval_intent.py` → `reports/intent_eval.json`)

```bash
python scripts/train_intent_model.py   # rebuild the weights (byte-identical for the same seed)
python scripts/eval_intent.py          # gold + held-out synthetic test, offline (no LLM calls)
```

SIMULATED / hand-written data only. **Gold** = `tests/data/intent_gold.jsonl`, 160 hand-written
messages (Hinglish, Devanagari, code-mixed, typos, sarcasm, voice-summary style, deliberately
ambiguous ones whose right answer is UNKNOWN), written independently of the training templates.
**Synthetic test** = the generator with seed 9001 (training uses 1729): same templates, so it only
checks plumbing. Reference day 2026-11-19.

| Gold (n = 160) | Intent stack | Current rules (`IntentClassifier`, no LLM) |
|---|---|---|
| Accuracy | **95.0%** | 33.8% |
| Macro-F1 | **95.6%** | 26.5% |
| Accuracy on the 9 legacy intents (projection) | **95.6%** | 59.4% |
| STOP recall | **100%** (16/16) | 56.3% |
| HARDSHIP recall (incl. 4 self-harm) | **100%** (16/16) | 43.8% |
| ABUSIVE recall | 100% (5/5) | 0% (no such intent) |
| Answered by tier 1 rules | 80.6% (acc 97.7%) | — |
| Answered by tier 2 small model | 10.0% (acc 100%) | — |
| Abstain → would reach an LLM tier | 9.4% (15 msgs; 10 of them are gold UNKNOWN, so abstaining was right) | — |
| Date entity exact match | 24 / 25 | — |
| Latency per message (this laptop) | p50 ≈ 0.5 ms, p95 < 20 ms; cached ≈ 0.01 ms | — |

Synthetic test (n = 1,020): 98.9% accuracy, rules 84.2% / model 9.4% / abstain 6.4%, STOP and
HARDSHIP recall 100%. The first gold run (before fixing seven rule gaps it exposed, e.g. "expired"
matching a payment link, "lost my father", "link bhi bhejo", "haath tang", emoji-only "👍") scored
91.9%; remaining errors are listed in the report. Known trade-off: STOP is high-recall, so
"paying now, don't call again today" is read as STOP (safe direction: it suppresses contact).

**Decision:** the stack beats the current rules on every criterion (accuracy, macro-F1, legacy
projection, STOP / HARDSHIP recall), so the **reply-ingestion path defaults to it**
(`intent_stack.STACK_DEFAULT_FOR_INGESTION = True`; `KAIROS_INTENT_STACK=0` switches back).
`intent.IntentClassifier` keeps its old behaviour unless `use_stack=True` (or `KAIROS_INTENT_STACK=1`),
because CM-03 is being built against its current outputs; its public API is unchanged
(`IntentResult.stack` and the `"model"` source are additive).

### Wiring

* **Reply ingestion (CM-02):** `OutcomeFinalizer.record_text_reply(borrower_id, at, text, channel)`
  classifies, then calls `record_reply(meaningful=...)`; meaningful = any reply except STOP and
  WRONG_NUMBER (K01 `reply_attribution`). Returns `(code, StackResult)`; the caller still routes
  STOP to `Ledger.record_stop` and K13 labels to `exceptions.handle`.
* **CM-03 (WhatsApp servicing):** keep calling `IntentClassifier(llm=LunaClient(), use_stack=True)
  .classify(InboundMessage(...))`. Buttons / DTMF / STOP keywords are handled exactly as before; free
  text goes through the stack with `llm` as the cloud tier and the borrower id redacted. Use
  `result.intent` for the 9-intent contract and `result.stack["label"]` for the 17 labels
  (BALANCE_QUERY, LINK_REQUEST, STATEMENT_REQUEST, LANGUAGE_CHANGE, HUMAN, ...). Treat
  `result.stack["needs_human"]` as the hand-off signal; never disclose before OTP whatever the label.
* **Voice summaries (K17/K18):** the Sarvam post-call `disposition` stays primary. Only when
  `disposition_confidence` is below the `policy/K17-voice-dispositions` threshold, or the
  disposition is free text, call `intent_stack.classify_reply(call_summary, at=call_end,
  channel="voice")` and use its label / `entities.date` (PTP date) / `needs_human`. The rules
  understand third-person summary style ("Borrower promised to pay on 25th").

### Optional: enable a local LLM (tier 3)

Nothing requires it — without it the stack runs rules + small model + (if a key is set) the cloud
tier. To try it on a dev machine:

```bash
brew install ollama            # or the installer from https://ollama.com/download
ollama serve &                 # listens on 127.0.0.1:11434
ollama pull qwen2.5:3b         # or: ollama pull llama3.2:3b
export OLLAMA_HOST=http://127.0.0.1:11434
export KAIROS_LOCAL_LLM_MODEL=qwen2.5:3b
python scripts/eval_intent.py  # offline eval ignores LLM tiers; use IntentStack.from_env() to try it
```

`IntentStack.from_env()` (also used by `classify_reply` and `IntentClassifier(use_stack=True)`)
probes `GET /api/tags` once; if the server or the model is missing, tier 3 is simply skipped.

### Context for Claude / how to continue

* Files: `kairos_backend/intent_stack.py` (tiers, rules, entities, redaction, Ollama adapter, cache),
  `kairos_backend/intent_corpus.py` (templates, seeded generator, trainer), `scripts/train_intent_model.py`,
  `scripts/eval_intent.py`, `tests/test_intent_stack.py`, `tests/data/intent_gold.jsonl`.
* Adding a phrase: add a regex to the label's `_r(...)` list (Devanagari without `\b`), add a gold
  line if it is a new kind of message, run `pytest tests/test_intent_stack.py` and `scripts/eval_intent.py`.
  Never weaken a safety rule to fix a precision error elsewhere.
* Adding training phrasing: edit `T` in `intent_corpus.py`, re-run `scripts/train_intent_model.py`
  (the test checks the committed weights reproduce byte for byte), then re-run the eval and commit
  the report. Keep train seed 1729 / test seed 9001 / calibration seed 2718 distinct.
* Thresholds (`RULE_ACCEPT`, `MODEL_ACCEPT`, `LOCAL_LLM_BELOW`, ...) are part of the deterministic
  contract: change them only with a new `STACK_VERSION` and a fresh eval.
* LLM rules (CLAUDE.md): classification only, temperature 0 / reasoning none, strict schema, no
  tools, masked input. The cloud tier reuses `LunaClient`; do not add a second cloud path.
* Open follow-ups: a real LLM-tier eval (needs Ollama or a key; the report counts how many messages
  would reach one), a held-out half of the gold set for future rule tuning, and wiring
  `record_text_reply` into the CM-03 inbound webhook once that lands.
