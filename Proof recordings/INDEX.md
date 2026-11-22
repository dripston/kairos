# Kairos proof recordings: index

These are real-time screen recordings of the real product doing real things, recorded on 20 Nov 2026
(IST) for the CreditNirvana Agentathon. Use them as the backup if the live demo fails, and as evidence
that it works. They are not edited commercials.

- **What is on screen:** headless Chrome showing the actual pages, captured with Chrome's own screencast
  at 1920×1080 and 30 fps (the page at 112.5 % zoom).
  - The cursor dot and click ripple mark real mouse clicks sent to the page.
  - Nothing is mocked in the page. Borrowers, replies and the bank feed are SIMULATED, as the product
    itself labels them, and channels are dry-run.
- **Top bar:** LIVE (deployed) or LOCAL (same code on this machine), the host, and the real wall-clock time
  of the frame on screen. It shows `real time 1×`, or an amber `2× · wait sped up` badge while a wait
  (the server computing) is compressed. R10's test-suite run is compressed 4× and badged the same way.
- **Caption band:** plain-words narration below the page. It never covers the page.
- **Contact sheet:** each `_sheet.jpg` shows one frame every 5 s.
- **Live-demo steps:** `RUNBOOK.md` has the live steps per flow, with fallback timestamps.

| # | File | Length | Where | Recorded | What it proves |
|---|---|---|---|---|---|
| R01 | [`R01_outbound_vikram_to_payment.mp4`](R01_outbound_vikram_to_payment.mp4) · [sheet](R01_outbound_vikram_to_payment_sheet.jpg) | 3:07 (waits at 2×, 5 s saved) | **LOCAL** · 127.0.0.1:8120/demo/journey/outbound | 2026-11-20 10:29:15 IST · 37 MB | Journey 1 runs end to end on the deployed engines. Vikram goes from day 1 to bank-verified payment: Auto-churn placement, the Learner's channel × time choice, Aladdin's rule check and reserve, a mock send, a duplicate receipt ignored, a no-reply update (SMS 8.0 → 4.6 in 100), a switch to WhatsApp, a Hindi promise to pay read by the intent rules, quiet days, then paid with 2 messages for ₹0.65. Re-recorded on the release-candidate server (no vendor name on screen); the earlier LIVE take is kept as `R01_outbound_vikram_to_payment_live_old.mp4`. |
| R02 | [`R02_outbound_human_control.mp4`](R02_outbound_human_control.mp4) · [sheet](R02_outbound_human_control_sheet.jpg) | 3:25 (waits at 2×, 3 s saved) | **LOCAL** · 127.0.0.1:8120/demo/journey/outbound | 2026-11-20 10:33:33 IST · 45 MB | A person stays in control and Aladdin still rules. An SMS at 8 pm is blocked (contact hours, locked by guardrails), refused, and audited. A 2:30 pm SMS is allowed but needs maker-checker. A voice-bot call is approved (re-checked → call → paid); in a second run the same call is rejected. Then Force WAIT, Hold/Release, PAUSE ALL OUTBOUND (40 queued cancelled) → Resume, Pause campaign → Resume, and Mark STOP (two clicks). The system is left resumed. Re-recorded on the release-candidate server (no vendor name on screen); the earlier LIVE take is kept as `R02_outbound_human_control_live_old.mp4`. |
| R03 | [`R03_outbound_technical_view.mp4`](R03_outbound_technical_view.mp4) · [sheet](R03_outbound_technical_view_sheet.jpg) | 3:04 | **LOCAL** · 127.0.0.1:8120/demo/journey/outbound | 2026-11-20 10:38:06 IST · 51 MB | The same journey with Technical on. Shows the score grid (posterior, Thompson draw, chosen cell), the K01 rule order and reserve, raw receipt/dedupe events, the posterior update with n before/after, the intent tier, label and confidence, churn rule ids, the executor's dry-run details, and day-4 per-channel blocks. Re-recorded on the release-candidate server (no vendor name on screen); the earlier LIVE take is kept as `R03_outbound_technical_view_live_old.mp4`. |
| R04 | [`R04_inbound_full_story.mp4`](R04_inbound_full_story.mp4) · [sheet](R04_inbound_full_story_sheet.jpg) | 2:28 | **LIVE** · kairos-backend-5ifc.onrender.com/demo/journey/inbound | 2026-11-20 04:24:52 IST · 38 MB | CM-03 inbound servicing on the deployed engine. OTP before any disclosure, then the answer and a signed pay link. An edited screenshot is caught and not marked paid. Take over → Reject → Hand back. An injection is refused before any model. Borrower B's hardship: 4 queued cancelled, routed to a person; Take over, reply, 30-day hold, hand back. STOP: every channel opted out, 0 queued. The server trace count matched the script (14). |
| R05 | [`R05_live_dashboard_race.mp4`](R05_live_dashboard_race.mp4) · [sheet](R05_live_dashboard_race_sheet.jpg) | 3:33 (waits at 2×, 17 s saved) | **LIVE** · kairos-backend-5ifc.onrender.com/demo/live | 2026-11-20 04:39:50 IST · 30 MB | /demo/live comparison run on the deployed server (seed 42, 300 synthetic borrowers, 14 days): Kairos vs the fixed schedule on the same budget and rules. The activity feed, the day-7 WhatsApp drop, and the day-12 callout (moved to SMS). Final numbers for this demo run: ₹1,76,723 vs ₹17,938, cost ₹471, rule breaches 0. These are simulation results, not a forecast. |
| R06 | [`R06_full_system_trace.mp4`](R06_full_system_trace.mp4) · [sheet](R06_full_system_trace_sheet.jpg) | 3:34 | **LIVE** · kairos-backend-5ifc.onrender.com/demo/live?interactive=1 | 2026-11-20 04:47:05 IST · 27 MB | /demo/live full-system trace on the deployed server: the System page lights up per component, approvals are approved and rejected on screen (interactive), and one borrower's full trace is opened. |
| R07 | [`R07_engine_room_churn_learning.mp4`](R07_engine_room_churn_learning.mp4) · [sheet](R07_engine_room_churn_learning_sheet.jpg) | 2:05 | **LOCAL** · 127.0.0.1:8095/demo/engines · branch demo/engine-room @ c3abbd8 | 2026-11-20 04:35:15 IST · 44 MB | Engine Room (branch demo/engine-room, local): the K27 churn moves stream live and the K09 estimates update. The drift lands on day 7; estimates fall from day 9; at the end of day 14 the 1–30 segment switches WhatsApp → SMS (13.5 → 9.7 in 100; SMS 0.27 vs 0.24 replies per ₹). One churn move is opened in Technical with its rule card. |
| R08 | [`R08_frontend_tour.mp4`](R08_frontend_tour.mp4) · [sheet](R08_frontend_tour_sheet.jpg) | 2:18 | **LIVE** · dripston.github.io/kairos → kairos-backend-5ifc.onrender.com | 2026-11-20 04:45:11 IST · 17 MB | **Cut version** (three passages that showed the voice vendor's name were removed whole: the CRM manual-send step, the Voice bots step and the Live journey step; the top-bar clock jumps at each cut). The GitHub Pages frontend on the live API: Control Room (approvals queue approve/reject), Smart CRM borrower (Explain, manual-send preview through the Gate), Cohorts, Campaigns, Voice bots, Learning & Proof, Live journey. |
| R09 | [`R09_cm03_console.mp4`](R09_cm03_console.mp4) · [sheet](R09_cm03_console_sheet.jpg) | 1:44 | **LOCAL** · 127.0.0.1:8101/cm03/console | 2026-11-20 04:50:36 IST · 19 MB | The CM-03 console, playing the borrower: hi → OTP → Hinglish balance → promise to pay with a date → pay link → OTP-scam message → injection (security hand-off) → holding reply while a person owns it → STOP still honoured. The state panel and CRM envelopes are shown. |
| R10 | [`R10_proof_terminal.mp4`](R10_proof_terminal.mp4) · [sheet](R10_proof_terminal_sheet.jpg) | 1:38 (test run at 4×, 79 s saved) | **LOCAL** · local checkout · kairos-main @ 997260d | 2026-11-20 05:06:16 IST · 32 MB | Real command output on the local checkout at 997260d, replayed at captured timing: red team 49/49 PASS; 50,000 atomic decisions in 11.2 s (target < 600 s); pytest 3094 passed, 1 skipped (shown at 4×); K09 off-policy evaluation; conformal threshold calibration. |

## Vendor name on screen

Every recording was OCR-scanned (macOS Vision, one frame every 1 s) for the voice vendor's name:

- Clean: R01–R03 (release-candidate takes), R04–R07, R08 (cut), R09, R10.
- The **old LIVE takes**, kept only as backups:
  - `R01_outbound_vikram_to_payment_live_old.mp4` shows it at 1:51–2:14, in the "Prism (…)" tier box in Intent stack.
  - `R02_outbound_human_control_live_old.mp4` shows it at 1:40, in the same box.
  - `R03_outbound_technical_view_live_old.mp4` shows it at 2:15–2:46, in the same box in the Technical view.

## How these were made (to re-record)

The tools are in `work/recorder/` next to this folder.
- `rec.py` drives headless Chrome over the DevTools pipe and records `Page.startScreencast` frames with
  wall-clock times.
- `rNN.py` is the click script for each recording. The scripts are reactive: they narrate the events the
  page actually shows.
- `assemble.swift` turns the frames into a constant-30 fps H.264 MP4 (it repeats the last frame where
  nothing repainted) and makes the contact sheet.
- `termrec.py` + `term_page.py` capture and replay the terminal for R10.

```bash
cd work/recorder
python3 r01.py && ./assemble runs/R01/log.json ../../proof_recordings/R01_outbound_vikram_to_payment.mp4 ../../proof_recordings/R01_outbound_vikram_to_payment_sheet.jpg
JB=http://127.0.0.1:8100 python3 r02.py     # any outbound/live/CM-03 script against a local server (labelled LOCAL)
```
