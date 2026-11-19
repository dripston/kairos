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

- K09 learner + outcome finalizer, K06 SupervisorFSM, intent/propensity, K09 replay — first built on
  the pre-spine ledger (PRs #2–#5); being **ported onto the spine-based ledger** on `k09/on-spine`.
- K09 borrower-context layer: learned reply multipliers by engagement (new / missed once / missed 2+ /
  replied) × recency (contacts in last 7 days). This is what moves response-per-rupee: repeat
  non-responders reply ~2–2.5× less than fresh borrowers on the DEV simulator.
- `crm/` (PR #6): K05/K14 borrower page + shared event ingestion + Sarvam IVR/voicebot flows;
  React wiring of the CRM page is in a separate PR.
- K22 template selector, K20 email channel, K07/K08 channel adapters (mocks + phone-demo adapters):
  separate branches/PRs.
- Sarvam voice agents need the `sarvam-voice-agents` MCP authorised; specs are in
  `crm/kairos/voice/agents/*.json`.

## How to continue a PR

1. Read this file, the PR description and the module README it touches.
2. Rebase on `main`; run `pytest` (and `npm run build` for frontend changes).
3. Keep the matched-comparison rules for any metric claim: same seed, portfolio, caps and budget;
   report several seeds, not one; never tune against the simulator's hidden parameters.
4. State in the PR what is verified, what is SIMULATED and what is still blocked (credentials,
   official datasets D1–D13, simulator API, hidden seed).
