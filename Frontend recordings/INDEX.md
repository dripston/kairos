# Kairos frontend recordings (F series)

These are clean, real-time recordings of the deployed React frontend
(https://dripston.github.io/kairos, connected to https://kairos-backend-5ifc.onrender.com) running each case.
They are deliverables and source footage for the films.

## Format

- **Video:** 1920×1080 at 60 fps, H.264. Chrome's own screencast frames at their compositor times, with no
  re-timing; where nothing repainted, the last frame is held. Full page, no browser chrome, light theme
  (`?theme=light`), page at 112.5 % zoom (F03 and F04 at 125 %).
- **Pointer:** an eased arc glide between targets, with a click ripple and human pauses (0.8–1.2 s before a
  click, ≥ 2 s on a result). Clicks are real mouse events. Scrolling is eased; there are no jumps.
- **`_clean.mp4`:** the page only, for the film editor.
- **`_captioned.mp4`:** the same frames with plain-English captions (white with a 3 px dark outline and a
  soft shadow; transparent, no box). The bottom line shows the LIVE label, the host and the real
  wall-clock time; an amber **SIMULATED panel** tag appears while a panel runs on the page's in-browser
  simulation.
- **`_sheet.jpg`:** one captioned frame every 5 s.

All borrowers are synthetic and every channel is dry-run; nothing reached a real person. Holds, pauses and
approvals made during a take were resolved in the same take.

## Cases

### F01 · Control Room tour, slow

- **Files:** [`F01_control_room_tour_clean.mp4`](F01_control_room_tour_clean.mp4) · [`F01_control_room_tour_captioned.mp4`](F01_control_room_tour_captioned.mp4) · [sheet](F01_control_room_tour_sheet.jpg)
- **Length:** 1:46, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:42:16 IST · LIVE · deployed
- **Steps:** Overview (KPIs, money collected, sends by channel, replies per ₹, region/cohort, Needs you, How you step in, Today's plan, cohort table) → Live activity (Aladdin's decisions, ✨ Explain one, live event feed, approvals queue, CM-03 summary, guardrail & audit log) → Budget & channels.
- **Proves:** The operator's whole screen, with the live backend panels next to the simulated ones.
- **Data:** Overview tab and the guardrail & audit log card: **SIMULATED panel** (in-browser simulation; tagged in the captioned version). Live activity: decisions, event feed, approvals: **API** (`/audit`, `/hitl/approvals`).
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F01b · Kill switch: pause all outbound, then resume

- **Files:** [`F01b_kill_switch_clean.mp4`](F01b_kill_switch_clean.mp4) · [`F01b_kill_switch_captioned.mp4`](F01b_kill_switch_captioned.mp4) · [sheet](F01b_kill_switch_sheet.jpg)
- **Length:** 0:27, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:42:16 IST · LIVE · deployed
- **Steps:** Control Room header → **Pause all outbound** (reason, confirm) → toast "Backend: all outbound paused · 0 queued touches canceled" → **Resume all outbound** → "Backend: outbound resumed · Aladdin replans, every guardrail re-checked".
- **Proves:** One click stops every channel on the backend; resuming re-checks every guardrail.
- **Data:** **API** (`/control/outbound` on the deployed backend; deployed audit rows 244 OUTBOUND_PAUSED 10:44:06 and 245 OUTBOUND_RESUMED 10:44:20 IST, 0 touches cancelled). Cut out of the F01 take, real time. Do not repeat this against the deployment before the talk; use the local release candidate.
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F02 · Approvals: a person decides held voice calls

- **Files:** [`F02_approvals_supervisor_clean.mp4`](F02_approvals_supervisor_clean.mp4) · [`F02_approvals_supervisor_captioned.mp4`](F02_approvals_supervisor_captioned.mp4) · [sheet](F02_approvals_supervisor_sheet.jpg)
- **Length:** 1:13, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 09:46:44 IST · LIVE · deployed
- **Steps:** Live activity → approvals queue with 2 held voice calls → as Operator, Approve is locked → switch to **Supervisor** → approve FILM-VC-5 (toast "Approved FILM-VC-5 · RESERVED (as Supervisor)") → reject FILM-VC-6 ("Rejected FILM-VC-6 · CANCELED") → the backend audit entry in the live event feed (reason "Demo / test") → back to Operator.
- **Proves:** Human-in-the-loop with role permissions; Aladdin re-checks on approval; reject releases budget; every action is audited with role and reason.
- **Data:** **API**. Setup: the deployed queue was empty, so just before the take two voice calls were queued through the engine's own `POST /decide`: FILM-VC-5 (IVR, BRW-SL100040, 28 Nov 11:00) and FILM-VC-6 (voice bot, BRW-SL100030, 28 Nov 12:00). Aladdin held both (VOICE_CHANNEL). Nothing is left pending.
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F03 · Smart CRM: a borrower, Explain, manual-send preview

- **Files:** [`F03_crm_explain_manual_preview_clean.mp4`](F03_crm_explain_manual_preview_clean.mp4) · [`F03_crm_explain_manual_preview_captioned.mp4`](F03_crm_explain_manual_preview_captioned.mp4) · [sheet](F03_crm_explain_manual_preview_sheet.jpg)
- **Length:** 1:01, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 09:52:47 IST · LIVE · deployed
- **Steps:** Smart CRM list → open BRW-SL100032 → next action, consent, EMIs → ✨ **Explain** ("Why Aladdin decided this", AI-generated) → manual SMS preview on **28 Nov 11:00 → SEND · ELIGIBLE · needs MANUAL_DISPATCH · nothing reserved** → **20:00 → WAIT · CONTACT_WINDOW · locked by guardrails**.
- **Proves:** Every decision is explainable; Aladdin's Gate can be dry-run on any send; quiet hours cannot be overridden by anyone.
- **Data:** **API** (`/borrowers`, `/ai/explain`, `/hitl/manual/preview`). Recorded at 125 % zoom so the CRM voice-bot card (it names the voice vendor) stays below the fold. Dates before 23 Nov return OUTSIDE_CAMPAIGN_PERIOD.
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F04 · Hold / resume contact on one borrower

- **Files:** [`F04_hold_resume_borrower_clean.mp4`](F04_hold_resume_borrower_clean.mp4) · [`F04_hold_resume_borrower_captioned.mp4`](F04_hold_resume_borrower_captioned.mp4) · [sheet](F04_hold_resume_borrower_sheet.jpg)
- **Length:** 0:45, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:14:17 IST · LIVE · deployed
- **Steps:** Smart CRM → BRW-SL100035 → Borrower actions → **Pause contact** (reason, confirm) → next action becomes WAIT (supervisor hold) → **Resume contact** → hold gone; consent, caps and quiet hours still apply.
- **Proves:** A person can stop one borrower instantly, and the hold is visible everywhere; resuming does not bypass any guardrail.
- **Data:** **API** (`/hitl/borrowers/{id}/actions`). The hold was removed at the end (checked via `/borrowers`).
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F05 · Live journey, end to end

- **Files:** [`F05_live_journey_clean.mp4`](F05_live_journey_clean.mp4) · [`F05_live_journey_captioned.mp4`](F05_live_journey_captioned.mp4) · [sheet](F05_live_journey_sheet.jpg)
- **Length:** 1:30, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:25:13 IST · LIVE · deployed
- **Steps:** Run the journey (POST /demo/journey/run on the deployed backend) → steps 1–10: pre-due, NACH bounce, STOP on every channel, CM-03 OTP + fake UTR, hardship, human approval, payment verified, duplicate ignored, learner update, day-end report.
- **Proves:** One borrower through every engine, from the backend's own scripted run.
- **Data:** **API** (the run comes from the backend; the page says its source on screen).
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F06 · Voice bots: AI voice agents

- **Files:** [`F06_voice_agents_clean.mp4`](F06_voice_agents_clean.mp4) · [`F06_voice_agents_captioned.mp4`](F06_voice_agents_captioned.mp4) · [sheet](F06_voice_agents_sheet.jpg)
- **Length:** 0:48, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:47:01 IST · LIVE · deployed
- **Steps:** Registry (use case and band → agent, voice; hardship triage needs a person) → languages → pick an agent for BRW-SL100014 (selection only) → disposition thresholds (below 0.6 nothing changes) → post-call webhooks with the rule fired and the action.
- **Proves:** Kairos, not the bot, decides whether a call may happen and what an outcome changes.
- **Data:** **API** (`/voice/registry`, `/voice/select`, `/audit` voice dispositions). Recorded after the frontend redeploy at 43caf21 (vendor-neutral labels); OCR at 0.1 s found no vendor name.
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F07 · Learning & Proof

- **Files:** [`F07_learning_proof_clean.mp4`](F07_learning_proof_clean.mp4) · [`F07_learning_proof_captioned.mp4`](F07_learning_proof_captioned.mp4) · [sheet](F07_learning_proof_sheet.jpg)
- **Length:** 0:59, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:16:15 IST · LIVE · deployed
- **Steps:** Slow scroll: K09 vs CN10 proof (REPLAY) → arm posteriors and channel switches → CM-02 daily report per segment → learning by cohort (what changed and why) → adaptive vs fixed cadence → cohort close.
- **Proves:** The learning policy beats a fixed cadence on the same seed, budget and caps, and you can see why.
- **Data:** Mixed: REPLAY and API panels; the arm-posteriors panel is labelled "SIMULATED · mock" (tagged in the captioned version).
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F08 · Cohorts and Campaigns (walkthrough)

- **Files:** [`F08_cohorts_campaigns_clean.mp4`](F08_cohorts_campaigns_clean.mp4) · [`F08_cohorts_campaigns_captioned.mp4`](F08_cohorts_campaigns_captioned.mp4) · [sheet](F08_cohorts_campaigns_sheet.jpg)
- **Length:** 1:05, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:18:33 IST · LIVE · deployed
- **Steps:** Cohorts: build a condition, Preview match (not saved), saved cohorts in priority order, campaigns plugged into cohorts → Campaigns: the list, New campaign wizard opened and cancelled.
- **Proves:** How a lender sets who Kairos works on; nothing changed.
- **Data:** **SIMULATED panel**: both pages run on the page's built-in simulation (labelled Synthetic data).
- **Vendor name on screen:** none (OCR, every 0.5 s)

### F09 · Engine Room (backend page)

- **Files:** [`F09_engine_room_clean.mp4`](F09_engine_room_clean.mp4) · [`F09_engine_room_captioned.mp4`](F09_engine_room_captioned.mp4) · [sheet](F09_engine_room_sheet.jpg)
- **Length:** 2:06, real time · 60 fps · 1920×1080 · light theme · recorded 2026-11-20 10:48:51 IST · LOCAL · release candidate
- **Steps:** The churn engine and the learning loop playing a 14-day, 300-borrower campaign; the day-7 drift; the end-of-day-14 switch; one churn move's rule card.
- **Proves:** Churn and learning, live, on the real engine code.
- **Data:** **LOCAL · release candidate** (http://127.0.0.1:8120/demo/engines).
- **Vendor name on screen:** none (OCR, every 0.5 s)

## Not recorded / pending

- **F01** is cut: the kill-switch passage was removed (it is F01b); the pointer position jumps at that cut.
- **System health model rows** (Budget & channels) are not dwelt on.
- **F02–F04, F07 and F08** were recorded before the 10:23 frontend redeploy (43caf21). They were OCR-checked
  and are free of the vendor name; the screens they use did not change visibly.
