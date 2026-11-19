# K27 — Auto-churn policy (DRAFT, not signed off)

Owner: Ojas (Product). Proposed by K27. Every file here is `status: DRAFT` until Product signs off.
Code: `backend/kairos_backend/churn/` (see the backend README, "Auto-churn engine (K27)").

Source: CreditNirvana "Collection Orchestration Engine" PRD v3 (cohorts from static + per-channel
disposition fields, coverage/orphans, one multi-day omnichannel journey per cohort, **two independent
event-driven rule engines** — risk reclassification and cohort reassignment — analytics, manual
scheduler) and CN's "Personal Loan 10-Day Post-Due Digital Collections Strategy" (C1–C5, T1–T4).

| File | What | Evaluated |
|---|---|---|
| `cohorts.json` | Named cohorts in priority order (first match wins) + live `UNASSIGNED` orphan bucket; initial risk tier by CN10 cohort | on every event and the daily tick (via `RA.RECOMPUTE`) |
| `risk_rules.json` | Risk tier LOW / MEDIUM / HIGH / CRITICAL moves on live events; cooldown 48 h, reversal (hysteresis) 120 h, hardship freeze | first matching rule that changes the tier |
| `reassign_rules.json` | Precedence (STOP, verified payment, hardship, K13 holds always win), outright `assign` rules with pins, `recompute` | precedence → manual override → rules → pin floor |
| `journeys.json` | Journeys per cohort (CN10 calendars verbatim + behaviour journeys), channel **layers** L1–L4 and their entry/exit rules, RL layer-recommendation acceptance rules, **stop rules** (lifecycle states) and no-response budgets | per event / tick |

## The machine per borrower

`cohort` · `risk_tier` · `journey` + day · `layer` (L1 cheap digital: SMS/email · L2 WhatsApp · L3 voice:
IVR/voicebot · L4 human) · `lifecycle` (ACTIVE, PAUSED_PTP, PAUSED_CLAIM, PAUSED_HOLD, HARDSHIP_HUMAN,
EXHAUSTED_CAMPAIGN, STOPPED_OPT_OUT, PAID_EXIT — transition table in `churn/fsm.py`).

## Condition language

`{"field": "...", "op": "...", "value": ...}` with `= ≠ > < ≥ ≤ contains not_contains between in not_in
exists`; `all` / `any` / `not` for grouping; `chain` for the PRD's strict left-to-right AND/OR rows.
Status fields (`sms.status`, `whatsapp.status`, …) are the PRD ordinal No data < Sent < Delivered < Read <
Clicked < Responded, so `"whatsapp.status" <= "Delivered"` means "not read yet". The full field list is
`FEATURE_CATALOGUE` in `churn/state.py` (static loan fields, per-channel status/days-since/counts over 3,
7, 14, 30 days, IVR answered/DTMF, voicebot disposition/confidence, CM-03 intent, PTP, payment, STOP,
consent, hardship, dispute, K13 hold, layer counters). Rules and cohorts may carry
`"scope": {"campaigns": [...], "cohorts": [...], "regions": [...]}`.

## Open decisions defaulted here (DRAFT — Product to confirm)

1. **Orphans are LIVE** (`UNASSIGNED`, recomputed); a frozen snapshot cohort is available and labelled as such (PRD §5.3 WATCH OUT).
2. **Stable ids, not names** key cohorts, journeys and overrides (PRD §5.4 WATCH OUT).
3. **Restart vs continue:** CN10 risk journeys CONTINUE at the DPD cycle day; behaviour journeys RESTART at day 1 on entry. A rule may force either.
4. **Pins are a priority floor:** a pinned cohort (e.g. WhatsApp-responsive for 7 days) yields only to a higher-priority cohort (promise, broken promise, claim, dispute, safety).
5. **Grouping:** PRD conditions are left-to-right without parentheses; `chain` implements exactly that, `all`/`any`/`not` add grouping.
6. **Day-0 tiers:** C1 MEDIUM (CN: "defaulted to medium"), C2 LOW, C3 MEDIUM, C4/C5 HIGH — so Day 0 reproduces CN10 cohorts exactly; the risk engine then moves borrowers between C2/C3/C4.
7. **Cooldowns:** 48 h between tier changes, 120 h before a reversal; strong event signals (verified/reversed payment, broken promise, refusal, hardship) bypass the cooldown; tick rules (no-contact, engagement, DPD floor) do not.
8. **Disputes:** K13 says outreach continues; the DRAFT journey is tier-1 digital only (L1/L2), no voice, no escalation.
9. **Layers are a ceiling:** the current layer is the most expensive layer automation may use; a step whose channel is above it is substituted with an allowed channel (recorded). `layer_mode: off` reproduces CN10 channels exactly.
10. **Manual schedule replaces** that day's automated step for that borrower (PRD §5.7 double-contact WATCH OUT); the K06 gate still decides.
11. **Overrides need maker-checker** (two different people), never beat STOP / paid / hardship / K13 holds, and expire.
12. **Exhausted:** no reply after the journey's no-response budget (attempts) in this campaign → EXHAUSTED_CAMPAIGN (handoff); the K09 RL `stop_policy` hook may supply a learned budget; a new campaign resets it.
13. **Payment claim:** pause 48 h (K17 `payment_claim.pause_hours`), then one verification follow-up; only D7/LMS verification exits.
14. **Contact window:** the PRD prototype's 08:00–18:00 is NOT used; K01's 08:00–19:00 applies (K06 gate), Sarvam schedules are set inside it.

## What Product must confirm

Cohort list and order; tier thresholds and cooldowns; which behaviour journeys exist and their days;
layer rules and the RL acceptance bar (20 observations, lift ≥ 1.10); no-response budgets; dispute
treatment; whether exhausted borrowers go to a person or the next campaign.
