# Problem-statement checklist (CM-02, CM-03, common gates)

Status as of 19 Nov 2026, evening. ✅ done with evidence · 🟡 done but with a caveat or still being
improved · ❌ not built yet. "PR #N" is on github.com/dripston/kairos. All numbers are SIMULATED.

## CM-02: self-learning omnichannel orchestrator

| # | Requirement (from the brief) | Status | Where / evidence |
|---|---|---|---|
| 1 | Each day, per borrower: no contact, or channel + time slot + template | ✅ | K06 SupervisorFSM + K09 learner (PR #11); templates (PR #8) |
| 2 | Respect per-borrower caps, consent and opt-outs | ✅ | K06 gate on main; red-team CAP, CON attacks pass (PR #14) |
| 3 | Opt-out on one channel stops all channels within one cycle | ✅ | Red-team CON-01, CON-02 pass |
| 4 | Only registered templates | ✅ | K22 catalog, 83 templates, 82 pass a 14-rule compliance check (PR #8); red-team TPL attacks pass |
| 5 | Ingest responses and update the policy | ✅ | Outcome finalizer: one reward per touch at the end of its 24 h window, idempotent (PR #11) |
| 6 | Best channel and time differ by borrower and change during a campaign | ✅ | Borrower-context layer + channel switches after WhatsApp drift (PR #11 replay output) |
| 7 | Over-contact lowers future response | ✅ | Recency buckets (contacts in last 7 days) and the K15 2-day gap (PR #11) |
| 8 | Stay within daily and campaign budget | ✅ | Ledger budget with atomic reserve; red-team BUD attacks pass |
| 9 | Report spend, response and learning per segment every day | ✅ | K26 daily per-segment report + lender sheets (PR #13) |
| 10 | **Hidden seed: response per rupee ≥ 20% above fixed cadence** | ✅ | Tuned on 4 seeds, chosen on 3 held-out seeds, then run once on 3 unseen seeds: **+48%, +61%, +56%** vs CN's 10-day cadence (PR #17, on top of #11). Re-run independently on seed 31337: +47.9%, deterministic. Caveats: spends 5–10% less than the budget, and STOP opt-outs are higher than CN10 (22 vs 10) |
| 11 | **Zero cap breaches** | ✅ | 0 K01 breaches and 0 K15 violations on every seed and both arms |
| 12 | 50,000 daily decisions in under 10 minutes | ✅ | About 2.1 min single run (402 decisions/s) |
| 13 | Deterministic given a seed | ✅ | `--check-determinism`: identical CRM envelope hashes |
| 14 | Human in the loop | ✅ | Approvals, holds, manual sends, maker-checker, audit (PR #12) |
| 15 | Voice channel with structured outcomes | ✅ | 6 Sarvam bots (Ritu, Advait), 8 languages, unified dispositions with confidence; Kairos-side registry, dispositions and confidence thresholds (PR #15, 1208 tests) |

## CM-03: inbound WhatsApp servicing agent

| # | Requirement | Status | Notes |
|---|---|---|---|
| 1 | ≥ 70% of conversations resolved without a human | ❌ | No inbound WhatsApp conversation agent in these PRs yet. Building blocks exist: intent classifier (PR #11), WhatsApp templates (PR #8), HITL handoff (PR #12) |
| 2 | Every seeded fake caught | ❌ | Needs the inbound agent plus a fake-detection check (fake payment screenshots, impersonation) |
| 3 | Zero disclosure before verification (OTP) | 🟡 | Voice bots and templates disclose nothing before identity; an OTP step for WhatsApp is Rehan's item |
| 4 | First reply in under 30 s | ❌ | Not measured yet |
| 5 | 500 concurrent conversations | ❌ | Not load-tested yet |

## Gates for every problem

| Gate | Status | Evidence |
|---|---|---|
| ≥ 3 real tool calls end to end | 🟡 | Sarvam bots exercised live by chat; phone-demo SMS and WhatsApp to the team's own numbers ready (dry-run until switched on); report generation |
| Red-team guardrail pass | 🟡 | 43 of 49 attacks pass with the HIGH fixes (PR #16 on top of #14); the 6 remaining are MED findings with owners |
| Synthetic data only | ✅ | Synthetic portfolio and borrowers; no real names or phones in the repo |

## Biggest gaps, in order

1. **CM-03 inbound WhatsApp agent** (rows CM-03 1 to 5).
2. CM-02 opt-outs: K09 triggers more STOP replies than CN10; add an opt-out risk term to the value score.
3. Merge the open PRs into main and run the full test suite once on the merged code.
4. Frontend still uses mock data in places; wire the Control Room and CRM to the API.
