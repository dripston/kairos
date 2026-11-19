# Integration branch (`integration/all`)

One branch where every open Kairos PR works together, plus a narrated end-to-end journey through
all of it. Everything it shows is **SIMULATED** (synthetic borrowers from the K03 generator, fixture
callbacks, the DEV simulator).

## Merge order

`scripts/integrate.sh` holds the list. Each branch is merged with `git merge --no-ff` at its current
tip, so GitHub marks its PR merged once `integration/all` lands on `main`.

| # | Branch | PR |
|---|---|---|
| 1 | `docs/claude-context` | #7 |
| 2 | `k09/on-spine` | #11 |
| 3 | `k09/optimize` | #17 |
| 4 | `k17-k18/voice-engine` | #15 |
| 5 | `k06/hitl-control` | #12 |
| 6 | `k26/report-pack` | #13 |
| 7 | `redteam/guardrail-suite` | #14 |
| 8 | `redteam/fix-high` | #16 |
| 9 | `redteam/fix-med` | #19 |
| 10 | `k07-k08/channel-adapters` | #10 |
| 11 | `k07-k08/android-sms` | #18 |
| 12 | `k22/template-selector` | #8 |
| 13 | `feat/k05-k14-crm-voice` | #6 |
| 14 | `k20/email-channel` | — |
| 15 | `cm03/whatsapp-servicing` | — |
| 16 | `k09/intent-stack` | — |
| 17 | `k27/auto-churn` | — |
| 18 | `k06/deterministic-states` | — |
| 19 | `frontend/live-api` | — |
| 20 | `k09/rl-v2` | — |
| 21 | `policy/ojas-pack` | — |
| 22 | `k06/fsm-fixes` | merged last, once it exists |

`film/first-cut` is docs only and stays out of this branch.

## Re-running it

```bash
scripts/integrate.sh --dry-run    # what would be merged
scripts/integrate.sh --resume     # usual case: merge new branch tips onto the current integration/all
scripts/integrate.sh              # full rebuild from origin/main, then replay the glue commits
```

- Missing branches are skipped. Branches already in HEAD are skipped too.
- `git rerere` is switched on, so a conflict resolved once on this machine resolves itself again,
  but only when the conflicting hunks are byte-identical. When a branch has moved, a full rebuild
  meets new conflicts, so `--resume` is the normal way to pick up new tips.
- An unresolved conflict stops the script with exit code 2 and lists the files. Resolve them, run
  `git add` and `git commit --no-edit`, then `scripts/integrate.sh --resume`.
- **Glue commits** are this branch's own fixes. Their subject starts with `integration: `. After a
  full rebuild they are cherry-picked from the previous tip, which the script saves as
  `refs/integrate/previous`. `--glue-from REF` takes them from another ref.
- Nothing is pushed by the script. Run the checks below, then `git push origin integration/all`.

## Checks (from `backend/`, Python 3.9 venv)

```bash
pytest                                                  # 2374 passed, 6 xfailed (FSM conformance findings, k06/fsm-fixes)
(cd ../crm && python3 -m pytest tests)                  # 28 passed
python3 scripts/run_journey.py                          # the journey (below)
python3 scripts/run_redteam.py                          # 49 attacks: 49 PASS
python3 scripts/run_k09_replay.py --borrowers 300 --days 5
python3 scripts/report_pack.py
python3 scripts/render_states.py                        # after merging anything that touches a state machine
```

The frontend is unchanged except `frontend/live-api` and one API-client line, the demo-recipient
delete. `npm run build` was **not** run because Node is not installed on the integrator's Mac. Run it
on a machine that has Node before the PR merges.

## Conflicts resolved, and how

Most conflicts were additive: README sections, `api.py` routes and imports, `policy/README.md`
rows. In each case both sides were kept. The rest:

| Where | Branches | Resolution |
|---|---|---|
| `ledger.py` `preview()` / `reserve()` | #11 (`exclude`), #12 (`reserve_hook`, PENDING_APPROVAL), #16 (`check_paise`) | `preview(ins, exclude=None)` keeps the RT-F01 bound. The touch insert keeps the HITL status/reason columns and uses the bounded cost. |
| `pii.scrub` vs `hitl.scrub` | #16 / #19 vs #12 | **One scrubber**: `kairos_backend.pii.scrub`, which `hitl` re-exports. It keeps the RT-F08/F09 rules (Indian numbers, the borrower's own name and number, e-mails, linear-time regexes) and HITL's international `+…` rule. HITL used to mask any run of 9+ digits; that no longer happens, so dates and 12-digit UTR/RRN references stay readable. |
| `api.py` HITL / reports / voice / demo / events / cm03 / churn | #12 #13 #15 #10 #19 live-api cm03 k27 | Every router and route kept. The HITL manual-send models got `le=MAX_PAISE` (RT-F01). |
| `test_reports.py` API fixture | #12 × #13 | The HITL hook now sends IVR seeds to PENDING_APPROVAL, so the fixture seeds history with the hook off and turns it back on. |
| `test_api.py` events feed | live-api × #19 (RT-F10) | `POST /spine/import` is now limited to `KAIROS_IMPORT_DIR`, so the test imports through the ledger instead. |
| K22 template tests | #8 was written before the spine | The fixture was moved to `tests.spine_helpers.add_loan` and paise. `templates.py` had no other callers. |
| K20 email channel | k20 was written before the spine | Ported to the spine `consents` table. STOP does the same writes as `record_stop`. The email consent version moved to a new `email_consent_versions` table, because the spine has no version column. Tests ported. |
| `pyproject.toml` | voice, cm03, intent-stack | Three `[tool.setuptools.package-data]` tables became one. TOML forbids duplicates, and pytest would not start. |
| `replay/k09.py`, `run_k09_replay.py` | #17 (options, STOP penalty) + rl-v2 + k27 (`kairos_churn` arm) | `self.learning = arm.startswith("kairos")`. Both learning arms get `KairosOptions`; `policy="rl2"` applies to the plain `kairos` arm only. `run_comparison(..., journeys=)` and every flag were kept. |
| `k09/rl-v2` re-merge | the branch was **rebased** after its first merge | Its own `rl/*.py` and `rl2_sweep.py` were taken from the new tip. Our side was kept in the replay. |
| `STATES.md` | deterministic-states, ojas-pack (K01 1.1.1), graceful shutdown | Re-rendered. The new touch transition `DISPATCHING --SHUTDOWN_GRACE_EXPIRED--> UNCERTAIN` is declared and exercised by the conformance harness. |
| `backend/README.md` | everyone | Union of the sections. The `pytest` line lists every suite. |

## Added on this branch (glue)

- **`scripts/run_journey.py` + `replay/journey.py`**: the crazy journey (next section) and
  `tests/test_journey.py`.
- **Graceful shutdown.** `kairos_backend/lifecycle.py` is wired into the FastAPI lifespan. On
  SIGTERM/SIGINT:
  1. New dispatch work is refused with a 503.
  2. Tick loops stop.
  3. In-flight dispatches get `KAIROS_SHUTDOWN_GRACE_S` (default 10) to finish.
  4. Leftovers are marked `UNCERTAIN`. They are never re-sent; `resolve_uncertain` asks the provider.
  5. A `SHUTDOWN` audit row is written with counts, the WAL is checkpointed and every Ledger connection
     is closed.

  A restart in the same process works. The optional `KAIROS_TICK_INTERVAL_S` loop runs the HITL
  expiry, the voice payment-claim pauses and the K27 tick. The `crm/` server stops cleanly on Ctrl-C
  or SIGTERM. Replay, red-team, report and journey scripts exit 130 on Ctrl-C, and they write reports
  as temp file + rename (`kairos_backend/safeio.py`). Tests: `backend/tests/test_shutdown.py` and
  `crm/tests/test_shutdown.py`.
- **PII.**
  - `DELETE /demo/recipients` takes the number in a JSON body.
  - `DELETE /demo/recipients/{hint}` accepts the masked form or the last 4 digits. It needs a
    unique match, otherwise 422. The old full-number path still works.
  - `GET /borrowers/{id}` masks the phone and drops the e-mail unless `X-Kairos-Role: policy_owner`.
- **`POST /demo/journey/run {seed}`** runs the quick journey on a fresh temp DB and returns the JSON
  shape the frontend's Live journey page plays.

## The crazy journey

```bash
cd backend && python3 scripts/run_journey.py                    # full: + red-team + small K09 vs CN10 replay (~15 s)
python3 scripts/run_journey.py --quick                          # the story only (~3 s; what the tests run)
python3 scripts/run_journey.py --export-frontend                # + frontend/public/journey/journey.json
```

It writes these files to `backend/reports/journey/` (gitignored):

| File | Contents |
|---|---|
| `journey.html` | Timeline, one row per event, coloured by kind, with provenance / policy / rule badges |
| `timeline.json` | Events and CRM envelopes |
| `journey.json` | Player shape: steps with story, borrower card and events |
| `summary.json` | Invariants and the deterministic hash |

The cast: borrower A has two loans; B, C and D have one each; a small pre-due cohort is added. They
come from the K03 generator and are shown by id only. Bot variables are shown with the first name
masked.

1. **Pre-due.**
   - K09 + SupervisorFSM pick a channel, slot and K22 template for each cohort member.
   - A's Pre-Due voice-bot touch waits in PENDING_APPROVAL (HITL). A supervisor approves it and the
     Gate runs again.
   - `select_bot` returns PRE_DUE_REMINDER (Ritu).
   - The Sarvam call-end fixture says PTP @ 0.9. That creates a PTP hold, and K27 moves A to PROMISED.
2. **STOP.** B replies STOP to the SMS. The next morning all 5 channels say `STOP_OPT_OUT`.
3. **Hardship.**
   - C reports a job loss. The intent stack says HARDSHIP, and the bot says HARDSHIP @ 0.55. That
     gives a hardship hold and a HITL handoff, and every channel waits.
   - On D2 an operator tries a Hardship Triage call during the hold. The Gate refuses it.
   - On D4 the hold is over. The triage call goes to PENDING_APPROVAL under three rules (voice,
     first send after hardship, manual dispatch). The supervisor approves, the Gate re-runs, and the
     call goes.
4. **Unsure STOP.** D's call says STOP_REQUESTED @ 0.45. Kairos fails closed: a supervisor hold and a
   review, with no opt-out written.
5. **Bounce and broken promise.**
   - A's NACH bounces, and the promise date passes unpaid. That gives BROKEN_PTP.
   - K27 moves A to the BROKEN_PTP cohort and layer L3, with the voice bot first. `select_bot` says
     PTP_FOLLOW_UP.
   - K09 sends a WhatsApp payment-link reminder.
6. **CM-03.**
   - A writes on WhatsApp. Nothing is disclosed before OTP.
   - After OTP the agent gives the balance.
   - A claims "already paid on 20 November" with a UTR that is not in D7 and is past T+2. The agent
     returns FRAUD_SUSPECT and hands off.
7. **Payment.** D7 verifies A's loan-1 EMI. Only that EMI's queued WhatsApp is cancelled. Loan 2's SMS
   still goes.
8. **Duplicates and learning.** The duplicate Sarvam webhook and the duplicate delivery receipt are
   ignored. Reply windows close, 12 outcomes are finalized, and 25 arm posteriors move (before and
   after are printed).
9. **Day end.**
   - The K26 per-segment report for each day.
   - Learner state, and cap breaches 0.
   - Red team: 49/49 PASS.
   - K09 vs CN10 on a small replay of 150 borrowers × 4 days. This is a sanity line only; use the
     multi-seed table in `backend/README.md` for claims.

Result for seed 42: 80 events, 34 CRM envelopes, cap breaches 0, journey hash `53c17cfcfd1a3e8f`. The
hash is identical on every run.

## Still pending

- **`k06/fsm-fixes`**: 7 transition bugs. The 6 strict xfails in `tests/fsm` flip when it lands.
  Merge it last.
- **`k27/auto-churn` export fix**: merged at 361ada1. The K27 Sarvam export now puts the reserved
  `instruction_id` in `user_identifier`, so K27 voice calls join their touches.
- **Frontend build**: not verified here (no Node).

## Decisions for a human

1. **Hardship triage during a hardship hold.**
   - A voice HARDSHIP creates a K13 hardship hold (5 days) and a HUMAN_HANDOFF case. It does not
     create a HARDSHIP case.
   - Resolving the handoff case does not lift the K13 hold, so the Hardship Triage bot cannot call
     until the hold expires: the Gate refuses it, as the journey shows.
   - Choose one: allow a human-approved HARDSHIP_TRIAGE call under a hardship hold, or have the voice
     ingester open a HARDSHIP case that a supervisor can lift. Owners: Ojas (K13) and Rehan.
2. **Scrubber scope.** The merged scrubber no longer masks bare 9+ digit runs, which HITL used to do.
   Payment references stay readable, and `+`-prefixed foreign numbers are still masked. Confirm.
3. **K20 email consent version.** It lives in `email_consent_versions`, because the spine has no
   version column. Decide: keep that table, or add a column to the spine `consents` table (Varun).
4. **`requires-python = ">=3.11"`** in `backend/pyproject.toml` contradicts the "runs on 3.9" rule.
   Everything here was run on 3.9.
5. **`GET /borrowers/{id}`** still returns the full name for every role; only the phone and e-mail
   are masked. Decide whether viewers should see initials.
6. **Sample data in `policy/K24-portfolio-intake/sample_upload_v*.csv`** (from `policy/ojas-pack`)
   uses a realistic name and the common "+91 98765 43210" example number. It is not from this
   branch, but the no-PII rule says to swap it for a synthetic name and a 0-prefixed number.
7. `frontend/public/journey/` is gitignored on `frontend/live-api`, so the exported `journey.json` is
   not committed. The page falls back to the API, then to its built-in mock.
