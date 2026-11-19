# CLAUDE.md — context for anyone (human or Claude) picking up Kairos work

Kairos is The "A" Team's entry for the CreditNirvana Agentathon (19–20 Nov 2026, presentations
**Fri 20 Nov 17:30 IST**, 7 min + 3 min Q&A). It solves **CM-02** (self-learning omnichannel
orchestrator) and **CM-03** (inbound WhatsApp servicing agent) over one synthetic borrower journey.

## What "winning" means (judging bars — optimise for these)

**CM-02 acceptance:** on the *hidden seed*, **response per rupee ≥ 20% above the fixed-cadence
baseline, with zero cap breaches**; 50,000 daily decisions in < 10 min; deterministic given a seed.
Brief essentials: decide per borrower per day *no contact* or *channel + time slot + template*;
respect per-borrower caps, consent, opt-outs; ingest simulator responses and update the policy;
stay within daily and campaign budget; report spend, response and learning per segment each day.
"The best channel and time differ by borrower and change over a campaign." "Over-contact lowers
future response." Only registered templates; opt-outs honoured across all channels in one cycle.

**CM-03 acceptance:** ≥ 70% resolved without a human, every seeded fake caught, zero disclosure
before verification (OTP), first reply < 30 s, 500 concurrent conversations.

**Gates for every problem:** ≥ 3 real tool calls end-to-end, red-team guardrail pass, synthetic data only.

## Repo map

| Path | What | Owner |
|---|---|---|
| `policy/K01-decision-policy/` | Outreach policy (DPD bands, caps, windows, holds, templates, budget). **Single source of truth — never copy it.** | Ojas |
| `policy/K13-operational-exceptions/` | Needs-time / PTP / callback / hardship / routed exceptions | Ojas |
| `backend/spine/` | K03 borrowers, consents, loans, EMIs (obligations), payments, events; money in **paise** | Varun |
| `backend/replay/`, `backend/scripts/` | K15 replay: fixed cadence vs learning policy on the DEV simulator, safety, load | Varun |
| `backend/kairos_backend/` | K06 gate, ledger (atomic reserve), supervisor, executor, API — reads the spine | Rehan |
| `frontend/` | React + Vite demo UI (Control Room, Campaigns, Smart CRM, Learning) — still mock data | Yuval (UI) |

Task IDs (K01…K28) and edge cases (E01…E19) come from the team tracker sheet; the spec doc is
"Kairos Epics". People: Ojas (product/policy), Yuval (PM + dev: script, CRM trace, WhatsApp UX,
K05/K14/K17/K18/K20/K22), Rehan (adapters, supervisor/executor, OTP/D7/LMS), Varun (data,
spine, policy learning, replay, metrics).

## Non-negotiable rules

- **Label evidence**: LIVE SANDBOX / REPLAY / SIMULATED. Every number from the DEV simulator or a
  fixture is SIMULATED and must say so in code, reports and UI.
- **Guardrails run twice**: at approval (reserve) and at send time (dispatch). Caps never breached.
- **Separate facts**: delivery/read/click = reach, never a reply; borrower intent ≠ verified payment;
  only D7/LMS-verified payment clears an EMI, and it cancels queued touches **for that EMI only**.
- **STOP** on any channel suppresses all channels from the next cycle; consent is per channel with a version.
- **One reward per executed touch**, finalized when its 24 h reply window closes; duplicates and late
  callbacks never change state; unresolved provider outcomes stay out of learning.
- **No PII** in logs, prompts or commits (mask phones; the PL_Dummy CSV is never committed).
- **LLM use is narrow**: `gpt-6-luna` only for ambiguous free-text intent + a short summary,
  reasoning effort `none`, strict JSON schema, no tools, redacted input. Everything else is deterministic.
- Python code must run on **3.9** (`from __future__ import annotations`), stdlib-first.

## Run and test

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest                              # must stay green
python scripts/run_replay.py        # K15 baseline vs reference policy
cd ../frontend && npm install && npm run dev
```

## Work in flight (check the open PRs before starting)

| PR / branch | What | State |
|---|---|---|
| #11 `k09/on-spine` | K09 learner, outcome finalizer, borrower context, K06 SupervisorFSM, intent/propensity, K09 replay vs CN10 on the K03 spine | Open. 4 seeds +19% to +39% replies/₹ (mean +30%), 0 breaches |
| #12 `k06/hitl-control` | Human in the loop: approvals, holds, manual sends, maker-checker, audit (`reserve_hook` in ledger) | Open, 229 tests |
| #13 `k26/report-pack` | HDFC report formats + CM-02 daily per-segment report | Open, 203 tests |
| #14 `redteam/guardrail-suite` | 49 attacks, 10 findings tracked as strict xfails | Open; HIGH fixes on `redteam/fix-high` |
| #10 `k07-k08/channel-adapters` | Channel adapters + phone-demo mode (runtime allow-list in `~/.kairos`, never committed) | Open |
| #8 `k22/template-selector` | 83 templates, 14-rule compliance checker | Open |
| #6 `feat/k05-k14-crm-voice` | `crm/` borrower page, shared ingestion, 5-step replay | Open |
| `k17-k18/voice-engine` | Bot registry (use case → agent, voice, language), unified dispositions, confidence thresholds, `/webhooks/sarvam/call-end` | In progress |
| `k09/optimize` | Hold-out-seed tuning of K09 | In progress |
| PRs #2–#5 | Pre-spine K09/K06 versions | Superseded by #11 |

Merge order that minimises conflicts: #7, #11, #12, #13, #14, #10, #8, #6. #11, #12 and #13 all touch
`ledger.py`/`api.py` additively; re-run `pytest` after each merge.

### Sarvam voice bots (CreditNirvana workspace; only touch agents named "Kairos - ...")

| Use case | Agent id | Voice |
|---|---|---|
| PRE_DUE_REMINDER | `Kairos---Pr-0282af37-ef9d` | Ritu |
| EMI_REMINDER_EARLY (K17, 1–30 DPD) | `Kairos---EM-15f2766f-4eba` | Ritu |
| CALLBACK (K18) | `Kairos---Ca-f080ffa8-2d1d` | Advait |
| MID_BUCKET_RESOLUTION (31–90 DPD) | `Kairos---Mi-edcd3099-61a2` | Advait |
| PTP_FOLLOW_UP | `Kairos---PT-07a93e21-1a9b` | Ritu |
| HARDSHIP_TRIAGE (human-approved only) | `Kairos---Ha-1f02cd05-dfa0` | Advait |

All six share one script skeleton (Persona, Environment, Objective, Speaking style, Facts,
Conversation guidelines, Guardrails; passive voice, no language rules in the prompt) learned from
CN's production collections bots. Inputs: `user_name, lender_name, emi_due_date, touch_id,
preferred_language, call_date, max_ptp_date` (+ `overdue_days`, `promise_date`, `callback_reason`
per use case). Post-call outputs: `disposition` (16-value enum, first-match order, safety first),
`disposition_confidence` 0–1, `ptp_date`, `ptp_amount_type`, `callback_at`, `hardship_type`,
`language_used`, `needs_human`, `call_summary`, `rule_path`. Kairos, not the bot, decides what a
disposition does, using the thresholds in `policy/K17-voice-dispositions/thresholds.json`.
Never put amounts, loan numbers or phones in call variables. Edit prompts with small
`prompt_edits`, test with `send_chat` on the draft, then commit a version.

See OPERATING_GUIDE.md (how to run every demo) and PROBLEM_STATEMENT_CHECKLIST.md (what is done).

## How to continue a PR

1. Read this file, the PR description and the module README it touches.
2. Rebase on `main`; run `pytest` (and `npm run build` for frontend changes).
3. Keep the matched-comparison rules for any metric claim: same seed, portfolio, caps and budget;
   report several seeds, not one; never tune against the simulator's hidden parameters.
4. State in the PR what is verified, what is SIMULATED and what is still blocked (credentials,
   official datasets D1–D13, simulator API, hidden seed).
