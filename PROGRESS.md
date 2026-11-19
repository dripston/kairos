# Kairos — what has been done

Status on **20 Nov 2026, morning** (presentation: today, 17:30 IST). All 17 merged PRs are listed below.
Every number is **SIMULATED** (DEV simulator, synthetic K03 portfolio, seeded fixtures) unless it says
LIVE SANDBOX or REPLAY.

| | |
|---|---|
| Demo UI (GitHub Pages) | **https://dripston.github.io/kairos/**: rebuilt on every green push to `main`; runs on labelled mocks until a public backend is set (see "Connecting the UI to the backend") |
| CI | `.github/workflows/ci.yml`: backend tests on Python 3.9 + 3.11, red team, CRM tests, frontend lint + build |
| What is left | [open issues #32–#51](https://github.com/dripston/kairos/issues), labelled `demo-day`, `blocked`, `decision-needed` |
| Acceptance status | [`PROBLEM_STATEMENT_CHECKLIST.md`](PROBLEM_STATEMENT_CHECKLIST.md) |
| How to run every demo | [`OPERATING_GUIDE.md`](OPERATING_GUIDE.md) |

## 1. Judging bars

### CM-02: self-learning omnichannel orchestrator

| Bar | Result | Evidence |
|---|---|---|
| Replies per ₹ ≥ 20% above fixed cadence | ✅ **+48% / +61% / +56%** vs CN's 10-day cadence on 3 unseen seeds | PR #11 + #17 (K09) |
| Zero cap breaches | ✅ 0 K01 breaches and 0 K15 violations on every seed, both arms | K06 gate + atomic ledger |
| 50,000 decisions < 10 min | ✅ about 2.1 min end to end; 50k `reserve()` in 20.5 s | K15 benchmark |
| Deterministic given a seed | ✅ identical envelope hashes (`--check-determinism`) | K15 replay |
| Caps, consent, opt-outs; STOP stops every channel in one cycle | ✅ | red team CAP / CON attacks |
| Only registered templates | ✅ 83 templates, 14-rule compliance check | PR #8 (K22) |
| Report spend, response and learning per segment daily | ✅ | PR #13 (K26) |

Caveats: K09 triggers more STOP opt-outs than CN10 (22 vs 10) and spends 5–10% under budget (#38).
The hidden-seed run waits on the official simulator (#36).

### CM-03: inbound WhatsApp servicing (seeded eval, 1,000 conversations)

| Bar | Result |
|---|---|
| ≥ 70% resolved without a human | ✅ 73.2% (99.5% of bot-resolvable) |
| Every seeded fake caught | ✅ 127 / 127, 0 false fraud flags (13 fake kinds) |
| Zero disclosure before OTP | ✅ 0 violations |
| First reply < 30 s | ✅ p95 1.6 ms engine time |
| 500 concurrent conversations | ✅ 0 errors, 0 cross-talk |

### Gates for every problem

| Gate | Status |
|---|---|
| Red-team guardrail pass | ✅ 49 / 49 attacks pass (PR #14 → #16 → #19) |
| Synthetic data only | ✅ (one sample-file clean-up open: #46) |
| ≥ 3 real tool calls end to end | 🟡 Sarvam models and bots were called live (PR #15, #30); SMS / WhatsApp sends are still dry-run (#32, #33) |

## 2. What was built, by tracker task

| Task | What exists now | PR |
|---|---|---|
| **K01** decision policy | Ojas's policy v1.1.1 (DPD bands, per-channel caps, 08:00–19:00 window, quiet days, holds, budget pool and pacing) in `policy/`, 38 / 38 conformance cases | #25 |
| **K03** data spine | Borrowers, consents, loans, EMIs, payments, events (money in paise); synthetic 50k generator; `apply_payment` / `reverse_payment` | #11, #28 |
| **K05 / K14** CRM trace | Borrower page with shared event ingestion; delivery, reply, intent and verified payment kept apart | #6, #20 |
| **K06** guardrails + executor | Pure Gate following `evaluation_order`; SQLite ledger with atomic check-and-reserve; second check at send time; uncertain sends resolved with the provider; SupervisorFSM; human in the loop (approvals, holds, manual sends, maker-checker, audit) | #11, #12 |
| **K06** state machines | 19 lifecycles as deterministic state machines with conformance tests; the 7 findings fixed | #24, #26 |
| **K07 / K08** channels | Realistic mocks for every channel; phone-demo SMS / WhatsApp / Android SMS to an allow-list kept out of the repo (dry-run) | #10, #18 |
| **K09** learning | Contextual bandit (Beta priors, Thompson sampling), evidence decay 0.7, value floor; one reward per touch at the end of its 24 h window; CN10 baseline replay | #11, #17 |
| **K09** message understanding | Rules → small offline model → optional model tier; 95.8% on hand-written messages, 100% STOP / hardship recall | #22, #30 |
| **K10–K12** CM-03 | Inbound WhatsApp engine: OTP, payment-claim checks (doctored screenshots, reused UTRs, injected instructions), tickets, 500-way concurrency | #21 |
| **K13** exceptions | Needs time / PTP / callback / hardship / routed exceptions, 25 / 25 cases | #25 |
| **K15** safety, load, replay | Fixed cadence vs learning policy on the DEV simulator, load benchmark, determinism check | #11, #28 |
| **K17 / K18** voice | 6 Sarvam bots (8 languages); unified dispositions with confidence thresholds; Kairos, not the bot, decides what a disposition does | #6, #15 |
| **K20** email | Sandbox SMTP adapter, labelled REPLAY callbacks | #9 |
| **K21** propensity | Ojas's scorecard implemented and tested (20 / 20); wiring into the Supervisor is a product decision (#42) | #25 |
| **K22** templates | Approved-template selector, 83 templates, 14-rule compliance checker; Ojas's 300 T01 templates imported, not live (#43) | #8, #25 |
| **K23 / K26 / K27** review, reports, dashboard | Cohort review, HDFC lender sheets, CM-02 daily per-segment report, auto-churn engine (cohorts, risk, reassignment, stop states) | #13, #23, #28 |
| **K24** portfolio intake | Ojas's intake rules (32 / 32); `POST /intake/preview` used by the Campaign wizard's CSV upload | #25, #31 |
| **K25** decision log | Every decision, check and provider status in the audit log, with rule and policy version | #11, #12 |
| **K28** access | Phones masked except for the policy owner; read-only viewers see initials only | #12, #31 |
| Models (optional) | Sarvam and DeepSeek models behind validators; all off without a key; none decides a transition, guardrail, money or consent | #30 |

## 3. Demo UI (`frontend/`)

React 19 + Vite, dark operations console. A standalone app, not part of CognifAI.

- **Control Room:** Cohort / Campaign scope, collection KPIs (collected, collection %, average per day, verified payments, replies per ₹, cap breaches), alerts, Aladdin's decisions, live event feed, approvals queue, budget & caps, CM-03 console, 6-day collections trend, region scorecard, cohort-wise performance, drill-down queue, learning timelines by cohort, audit log.
- **Cohorts:** build cohorts from risk tag, DPD, region, EMI, bounces, NACH and last response; set priority order; attach campaigns.
- **Campaign Management:** cohort-first wizard; the CSV upload is checked by the K24 intake API.
- **Smart CRM:** portfolio KPIs and filters, plus a borrower page (journey, Aladdin's decision, guardrail checks, consent and reach, timeline, actions).
- **Learning & Proof:** live K09 arm scores and channel switches; learning by cohort, channel and region; benchmark vs fixed cadence.
- **Voice bots** and **Live journey** pages.
- Mentor feedback applied: cohorts with campaigns plugged in, decision-first dashboards, collections stats, learning by cohort / channel / region, and the Supervisor shown as "Aladdin" (PR #29).
- Every panel calls the API first and falls back to its own labelled mock per call.

### Connecting the UI to the backend

- **Locally (working):** seed with the journey, start uvicorn and point the UI at it. The steps are in `frontend/README.md` → "Local demo with live data". The header shows "API live · policy v1.1.1" and the panels show `SIMULATED · API`.
- **Deployed:** add `?api=https://<backend>` to the Pages URL, or set the repo variable `KAIROS_PUBLIC_API`. A permanently hosted backend is still to do (#34).

## 4. Engineering and process

- **Integration:** every open branch was merged through `integration/all` (PR #25), with the full journey green on the merged code.
- **Tests:** about 3,000 backend tests, 28 CRM tests and the 49-attack red-team suite, all green in CI on Python 3.9 and 3.11.
- **CI/CD (PR #31):** CI on every push and PR; Pages deploy after CI passes on `main`.
- **Repo:** policy packs live in `policy/` (single source of truth, never copied), backend in `backend/`, UI in `frontend/`, and context for anyone continuing in `CLAUDE.md`.
- **Hygiene:** no secrets or real PII in the repo; `.env` ignored; demo phone numbers stay in `~/.kairos`; phones masked in API responses and UI.

## 5. Merged pull requests (19 Nov)

| PR | What |
|---|---|
| #6 | K05/K14 CRM borrower page + event ingestion; K17/K18 Sarvam IVR and voicebot |
| #7 | CLAUDE.md: context, judging bars, rules |
| #8 | K22 template selector + catalog |
| #9 | K20 email channel |
| #10 | K07/K08 channel mocks + phone-demo adapters |
| #11 | K09 learner + K06 SupervisorFSM on the K03 spine, CN10 replay |
| #12 | K06 human-in-the-loop control plane |
| #13 | K26 report pack |
| #14 | Red-team guardrail suite |
| #20 | Frontend wired to the live API |
| #25 | Integrate everything (also carried #15–#19, #21–#24) |
| #26 | 7 state-machine findings fixed |
| #27 | `.env` ignored + example |
| #28 | K03 spine + K15/K23/K26/K27 |
| #29 | Frontend: mentor feedback |
| #30 | Sarvam + DeepSeek models behind guardrails |
| #31 | CI/CD + GitHub Pages; K24 intake preview; K28 viewer initials |

PRs #15–#19 and #21–#24 still show as open because they targeted feature branches, not `main`. Their code
is in `main` through #25. PRs #1–#5 were superseded by #11. Closing them is tracked in #50.

## 6. What is left

Tracked as issues #32–#51. The ones for today: live SMS / WhatsApp sends and the real-tool-call evidence
(#32, #33), a hosted backend (#34), the recorded demo with a backup (#35), and re-measuring the lift on
merged `main` (#40).
