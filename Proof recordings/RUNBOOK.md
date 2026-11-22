# Kairos live demo runbook (CreditNirvana Agentathon, 20 Nov, 17:30 IST)

Each section below is one flow you can run live. It gives the URL, what to click, what to say, what you
should see, and the recording to switch to if the live run fails, with the timestamp to jump to. The
recordings are real-time screen captures of the same flows, made this morning, most of them on the
deployed product. R7, R9 and R10 are LOCAL (same code), and each recording says which in its top bar.
They are not edited: waits on the server are sped up 2× and labelled on screen. `INDEX.md` lists every
file.

Backend: `https://kairos-backend-5ifc.onrender.com` (version 997260d). Frontend:
`https://dripston.github.io/kairos/control`.

## Before you start (T−15 min)

1. **Warm the server.** Render's free tier sleeps. Open `https://kairos-backend-5ifc.onrender.com/health`
   and wait for `{"ok":true,...}` (a cold start takes up to about a minute). Then open `/health/details`:
   `version` should read `997260d56c34`.
2. **Keep the outbound journey to yourself.** `/demo/journey/outbound` runs one session at a time. Any
   other Start (or `/api/close`) replaces yours mid-run, and the page then stops. During R1 recording an
   in-flight step hung once for 4+ minutes after something else closed the session. Ask the team not to
   test the deployment from 17:00.
3. **Browser.** Use Chrome on a 1920×1080 output at 110 % zoom (the recordings used a 1707×818 page,
   which is 112.5 %). Close other tabs and turn on Do Not Disturb.
4. **Have the fallbacks open.** Keep the `proof_recordings/` folder open in Finder or QuickTime, so you
   can switch to the recording in two seconds.
5. **Local fallback** (if Render is down). It runs the same code and works offline. Say "local" out loud
   if you use it.
   ```bash
   cd kairos-main/backend && source <venv>/bin/activate
   KAIROS_CM03_DEMO_INBOX=1 KAIROS_DB=/tmp/kairos_demo.db uvicorn kairos_backend.api:app --port 8000
   # then use http://localhost:8000/demo/... instead of the Render host
   ```
   The journeys build their own temporary databases, so they need no seeding. `/demo/live` and CM-03
   work the same way; `KAIROS_CM03_DEMO_INBOX=1` makes the CM-03 console show the OTP. For the Control
   Room's live panels, seed first, as Render's build does:
   `python -c "from replay.journey import run; run(seed=42, quick=True, echo=False, db_path='/tmp/kairos_demo.db', cohort_size=40)"`.

If anything fails live, say "This is the real deployment; here is the same flow recorded this morning",
and play the fallback recording from the timestamp given.

---

## R1 · Journey 1: Vikram, from day 1 to paid (the core story, about 3 min)

**URL:** `https://kairos-backend-5ifc.onrender.com/demo/journey/outbound`
**Fallback:** `R01_outbound_vikram_to_payment.mp4` (3:07, LOCAL · release candidate; the old LIVE take is `R01_outbound_vikram_to_payment_live_old.mp4`)

| # | Do | Say | You should see | Fallback at |
|---|---|---|---|---|
| 1 | Open the URL. | "A simulated 200-borrower campaign on Kairos's real engines. We follow one borrower." | Three story cards: Vikram, Aarav, Diya. | 0:00 |
| 2 | Click **Vikram**. | "Vikram is 10 days late on a ₹7,409 EMI. Each of the eight boxes is a real engine; it lights up when it acts for him." | Strip, eight boxes. Day 1 runs. Auto-churn: Cohort C4 · high risk · L3. | 0:04 |
| 3 | Wait for **Decision point · Day 1, 8:00 am** (it pauses on its own; the deployed server takes about 10 s to plan the first day). | "The learner picked SMS at 9:30; Aladdin checked every rule." | Yellow banner "Decision point". | 0:11 |
| 4 | Click **Learner**. | "It scores every channel and time against cost. People like Vikram reply most to SMS in the morning: about 8 in 100." | Channel SMS, Time 09:30, Reply chance ≈ 8.0 in 100. | 0:14 |
| 5 | Click **Aladdin**. | "Aladdin, our supervisor: consent, contact limits, quiet hours, budget, holds. Every rule passed; 25 paise reserved." | Five channel tiles (voice bot ✗ no consent), cost reserved ₹0.25. | 0:21 |
| 6 | Click **Follow live**, then **Play**. | "The mock SMS goes out. Dry-run: nobody receives it." | Executor: SMS sent · ₹0.25 · dry-run. Webhooks: Delivered. | 0:30 |
| 7 | When Webhooks shows **Duplicate receipt ignored**, press **Pause** (or Space). | "The provider sends the same receipt twice. The copy is ignored; nothing is counted twice." | Webhooks box: "Duplicate receipt ignored". Detail: "the copy is ignored, nothing changes". | 0:39 |
| 8 | **Play**. Pause at **Outcome → learning** (day 2, 9:31 pm). | "24 hours, no reply: the learner's SMS-morning estimate drops from 8.0 to 4.6 in 100." | Before/after bars. | 1:06 |
| 9 | **Play** to **Decision point · Day 3**. Click **Learner**. | "Now it picks WhatsApp at 9:30." | Contact: WhatsApp at 09:30 (about 16 in 100). | 1:22 |
| 10 | **Follow live**, **Play**. Pause when **Intent stack** lights up. | "He replies in Hindi: 'I'll pay by the 29th'. The rules read a promise to pay, 0.92. No model needed." | Intent: ptp (rules, 0.92) → ptp hold. | 1:42 |
| 11 | Click **Auto-churn**. | "Auto-churn moves him to PROMISED; Kairos goes quiet until that date." | C4 → PROMISED, L3 → L2, ACTIVE → PAUSED_PTP. | 1:53 |
| 12 | **Follow live**, **Play**, click **4×**. Back to **1×** on day 7. | "While the promise stands, Aladdin allows no channel." | Aladdin: "no channel allowed (promised to pay)". | 2:11 to 2:36 |
| 13 | It pauses by itself at the payment. Click **Payment**. | "Day 7: the bank confirms ₹7,409. Only bank-verified money counts. The journey exits. 2 messages, 65 paise." | Banner "Journey complete", status PAID · EXITED, Recovered so far updates. | 2:46 |

**If it fails:**
- *Nothing happens after clicking Vikram for more than 30 s.* The server is cold or busy. Reload once; if it
  still hangs, play R1 from 0:04.
- *"Step failed" toast, or the clock freezes.* Someone else started or closed the outbound session. Click
  **Restart** → **Vikram** once; if it fails again, play R1 from the step you were on.

## R4 · Journey 2: inbound WhatsApp servicing, the full story (about 2½ min)

**URL:** `https://kairos-backend-5ifc.onrender.com/demo/journey/inbound` (add `?script=1` for the cue cards)
**Fallback:** `R04_inbound_full_story.mp4` (2:28, LIVE)

| # | Do | Say | You should see | Fallback at |
|---|---|---|---|---|
| 1 | Click **Reset demo**. | "Left: the borrower's phone. Middle: what the real CM-03 engine did. Right: the agent console." | Toast "Demo reset: clean simulated world"; both borrowers show 4 queued · 4/4 channels on. | 0:05 |
| 2 | Borrower A → **When is my EMI due?** | "Until we know it is really them, nothing is disclosed. The question is parked; a code goes to the registered number." | "Identity first: a code went to the registered number…", about 1 ms. | 0:12 |
| 3 | **Reply with the code**. | "Verified: the parked question is answered from the loan system." | "Code correct: verified. The parked question was answered." | 0:21 |
| 4 | **Payment link**. | "A signed payment link." | "Sent a signed payment link for the overdue EMI (valid 24 hours)." | 0:29 |
| 5 | **I already paid ▾** → **Fake: edited screenshot**. | "Every check runs: Photoshop metadata, a hidden instruction, someone else's UTR. Nothing is marked paid." | "Fake proof caught … Nothing marked paid; a person reviews it"; red chips under "Caught: 4 checks failed". | 0:36 to 0:50 |
| 6 | Agent console: **Take over** on Borrower A. | "Asha takes over; the bot goes silent on this chat." | "You own it · bot silent". | 0:53 |
| 7 | **Reject claim**, then **Hand back to bot**. | "She rejects the claim, one audit row, and hands the chat back." | "Rejected the payment claim: it stays unpaid"; "Handed the chat back to the bot". | 0:59 / 1:05 |
| 8 | **Ignore your rules…** | "The injection detector fires before any model: refused, flagged." | "Injection attempt refused before any model saw it. Nothing disclosed; flagged for a person." | 1:10 |
| 9 | Tab **Borrower B** → **I lost my job, I can't pay**. | "The bot does not push for money: reminders paused, 4 queued cancelled, routed to a person." | "Hardship: reminders paused, 4 queued reminder(s) cancelled, and the chat routed to a person." | 1:18 to 1:21 |
| 10 | **Take over** (Borrower B) → **Suggested reply** → **Send**. | "Asha replies herself." | Her message appears on B's phone as "Asha · Kairos Demo Finance team". | 1:29 to 1:35 |
| 11 | **Hardship: hold outbound 30 days**, then **Hand back to bot**. | "Collections held for 30 days. Every step is in the audit log." | "Hardship confirmed: outbound collections held until …". | 1:44 / 1:50 |
| 12 | Tab **Borrower A** → **STOP**. | "Consent revoked on WhatsApp, SMS, voice and email. 4 queued → 0. The Gate blocks every channel." | Tab: "Opted out · 0 queued"; "STOP honoured on every channel". | 1:55 to 2:05 |
| 13 | Optional: **Technical**. | "The state names and raw envelopes behind it." | Technical trace. | 2:09 |

**If it fails:**
- *A chip does nothing.* The page is still busy with the previous message. Wait 2 s.
- *Odd messages appear.* Someone else is using the inbound demo. Click **Reset demo** and start again.
- *Server error toast.* Play R4 from the matching step.
- *Step 8 gets only a holding reply.* The chat was not handed back after step 7. Click **Hand back to bot**
  first.

## R2 · Journey 1: a person takes control (approve / reject, override, hold, pause, kill switch, STOP)

**URL:** `https://kairos-backend-5ifc.onrender.com/demo/journey/outbound`
**Fallback:** `R02_outbound_human_control.mp4` (3:25, LOCAL · release candidate; the old LIVE take is `R02_outbound_human_control_live_old.mp4`)

| # | Do | Say | You should see | Fallback at |
|---|---|---|---|---|
| 1 | Click **Aarav** (or **Restart** → Aarav). Wait for **Decision point · Day 1**. | "Aarav's story: a person can take control at every decision." | The learner plans an SMS at 17:30. | 0:00 to 0:11 |
| 2 | Take control → **or send** SMS, time **20:00**. | "I'm the supervisor. I try to send at 8 pm." | Red verdict: "Aladdin ✗ blocked · it is outside the allowed contact hours. Locked by guardrails: no person can override this." | 0:19 |
| 3 | Click **Apply**. | "Even if I insist: refused, and audited." | Toast "Refused…"; audit row "Override Aarav: SMS at 20:00 ✗ refused — locked by guardrails". | 0:27 |
| 4 | Change the time to **14:30**. | "A legal time passes every rule, but a manual send still needs a second person." | Green verdict "…every rule passes · then a second person approves (manual send · maker-checker)". | 0:35 |
| 5 | Set the time back to 09:30, then **Follow live**, **4×**, **Play**. Press ▶ again at each decision pause until the banner says **Waiting for you** (day 4). | "Calls always wait for a person." | Approvals panel: Aarav · Voice bot at 09:30, Approve / Reject. | 0:46 to 1:05 |
| 6 | Click **Approve**. | "Aladdin re-runs every rule at approval." | Toast "Approved — Aladdin re-ran every rule: still allowed"; audit row ✓ approved. | 1:12 |
| 7 | **Play**. | "The bot calls (mock). Aarav says he paid; the bank confirms it the same day." | "Journey complete", PAID · EXITED, ₹12.25 spent. | 1:23 to 1:39 |
| 8 | **Restart** → **Aarav**, 4× to day 4, then **Reject**. | "Rejected: no call, and the budget goes back to the pool." | Audit "Rejected Aarav's voice-bot call ✓ rejected". | 1:43 to 2:17 |
| 9 | **Force WAIT**. | "Cancel whatever is planned today." | Toast "Override applied: WAIT". | 2:21 |
| 10 | **Put on hold**, then **Release hold**. | "Aladdin answers SUPERVISOR_HOLD on every channel." | Toasts "On hold…" / "Hold released"; Aladdin box "hold is released". | 2:28 to 2:34 |
| 11 | **PAUSE ALL OUTBOUND**, then **RESUME ALL OUTBOUND**. | "One click cancels everything queued on every channel." | Toast "ALL OUTBOUND PAUSED · 40 queued touches cancelled…"; strip "OUTBOUND OFF", then "Outbound resumed". | 2:38 to 2:49 |
| 12 | **Pause campaign**, then **Resume campaign**. | "The campaign waits; nothing is lost." | "Campaign paused · N queued touches cancelled" (0 right after the kill switch), then "Campaign resumed". | 2:53 to 3:03 |
| 13 | **Mark STOP for borrower**, then click again within 3 s. | "A STOP turns every channel off; no person can undo it." | "STOP recorded: every channel off"; status STOP, controls disabled. | 3:06 to 3:13 |

**If it fails:**
- *The approval never appears.* "Pause at decisions" must be ticked, or someone else replaced the session.
  Play R2 from 1:05.
- *You left it paused.* Always finish with **RESUME ALL OUTBOUND** and **Resume campaign**. The strip
  should read "Running".

## R3 · Journey 1, Technical view

**URL:** same as R1. Press **T** or click **Technical** at any point.
**Fallback:** `R03_outbound_technical_view.mp4` (3:04, LOCAL · release candidate; the old LIVE take is `R03_outbound_technical_view_live_old.mp4`)

| Do | You should see | Fallback at |
|---|---|---|
| Vikram → Day 1 pause → **Technical** → **Learner** (scroll the detail) | The channel × time grid: posterior per 100, "draw", the **chosen** cell; CONTACT chip and replies per ₹ | 0:13 to 0:26 |
| **Aladdin** (scroll) | K01 rules in order, five channels checked, reserve: cost, pool left, today left, policy version | 0:26 to 0:40 |
| ▶, pause on the duplicate → **Webhooks** | Every webhook event with its dedupe reason, "State change: none" | 0:52 |
| ▶, pause on day 2's outcome → **Outcome → learning** | instruction id, band B1, slot 08-11, n 0 → 15, posterior 0.08 → 0.0457 | 1:22 |
| Day 3 pause → **Learner** | Updated grid; WhatsApp's morning cell is chosen | 1:42 |
| Intent → **Intent stack**, then **Auto-churn** | label PTP, tier rules, 0.92, action PTP_HOLD, decision model off in this demo; each churn move with its rule and trigger | 2:07 to 2:17 |
| **Executor**, then **Aladdin** on day 4 | Adapter, provider ref, "dry-run (mock)"; on day 4 every channel ✗ "promised to pay" | 2:37 to 2:46 |

Say: "Technical shows the same events with every id, so you can audit any decision."

## R5 · `/demo/live`: Kairos vs the fixed schedule

**URL:** `https://kairos-backend-5ifc.onrender.com/demo/live`
**Fallback:** `R05_live_dashboard_race.mp4` (3:33, LIVE)

| # | Do | Say | You should see | Fallback at |
|---|---|---|---|---|
| 1 | Run type **vs fixed schedule**, seed **42**, click **Start** (4× is the default). | "300 people missed an EMI. Kairos vs the usual fixed schedule: same people, same budget, same rules." | "Preparing · matching the budget" for about 30–40 s on Render, then Day 1. | 0:05 to 0:26 |
| 2 | Sidebar **Live activity**, then back to **Overview**. | "The activity feed says what happened in plain words." | Feed rows like "Ravi gets a WhatsApp at 5:30 pm". | 0:32 to 0:41 |
| 3 | Watch the chart. | "Only bank-confirmed money counts as recovered." | Two lines, Kairos above the fixed schedule. | 1:06 |
| 4 | Day 7. | "On day 7 WhatsApp stops working as well. Nobody tells Kairos." | | 1:40 |
| 5 | Wait for the callout (about day 11–12). | "By day 11–12 it has moved to SMS, from the replies alone." | Callout "Channel mix changed. WhatsApp stopped working as well from day 7, so Kairos moved to SMS…" | 2:35 |
| 6 | At the end. | Say "in this demo run", not as a forecast: "about ₹1.77 lakh vs ₹18 thousand, rule breaches zero." | ₹1,76,723 vs ₹17,938, cost ₹471, 0 breaches (seed 42 always gives these). | 3:13 |
| 7 | **Rules (Aladdin)**. | "What was held back and why." | Blocks by rule. | 3:19 |

Keys: **P** presenter mode, **S** cycles Overview → Live activity → System, **Space** pauses.
**If it fails:** if "Could not start" appears or it stays on Preparing for more than 90 s, play R5 from
0:26. Avoid saying "9.9×" as a claim; say "in this simulated run".

## R6 · `/demo/live`: full system trace

**URL:** `https://kairos-backend-5ifc.onrender.com/demo/live?interactive=1#system`. Without
`interactive=1`, a simulated supervisor decides the approvals.
**Fallback:** `R06_full_system_trace.mp4` (3:34, LIVE)

| # | Do | Say | Fallback at |
|---|---|---|---|
| 1 | System page → **Start full system trace**. If a run is already going, set Run type to "Full system trace" and press **Start**. | "The full trace runs every component: plan, Aladdin, executor, channels, webhooks, intent, learning, payments, reports." | 0:05 to 0:12 |
| 2 | Watch the boxes count up. | "Each box counts the real events it handled." | 0:12 |
| 3 | **Approvals** → **Approve** one, **Reject** one. | "Calls wait for a person; Aladdin re-checks approved ones; rejected ones release the budget." | 0:20 to 0:34 |
| 4 | **Borrowers** → click a row. | "One borrower, every event." The drawer shows the trace; Esc closes it. | 0:37 to 0:44 |
| 5 | Back to **System**. | | 0:54, then 1:25 onwards |

**If it fails:**
- *Slow.* The full trace is slow on the free server (day 6 after about 3 min). Start it before you need
  it, or play R6.
- *Approvals pile up.* With `interactive=1` every call waits for you. Approve them in a batch, or drop
  `interactive=1`.

## R7 · Engine Room: churn and the learning loop

**URL:** `http://127.0.0.1:8095/demo/engines` (LOCAL, branch `demo/engine-room` @ c3abbd8; not on Render
yet). Once merged and deployed it will be `https://kairos-backend-5ifc.onrender.com/demo/engines`.
**Fallback:** `R07_engine_room_churn_learning.mp4` (2:05, LOCAL)

| # | Do | Say | You should see | Fallback at |
|---|---|---|---|---|
| 1 | Open the page. The first load computes for about 10 s. | "The server runs 14 days for 300 synthetic borrowers on the real engines." | Veil "Running the campaign on the real engines…", then Day 1. | 0:00 |
| 2 | **4×**, **Start**. | "Left: the churn engine re-sorts people after every reply, payment, missed call and daily check. Right: the learning loop." | Moves stream under "Moves, live"; the loop strip counts. | 0:08 to 0:30 |
| 3 | **1×** before day 7. | "Day 7: WhatsApp replies drop to about a third. Kairos is not told." | Callout: "Day 7: the simulated world changes…" | 0:37 |
| 4 | Days 8–9 at 1×. | "Day 8's messages close their 24-hour window on day 9, so the estimates move from day 9." | WhatsApp bars fall per segment from day 9. | 0:45 to 1:00 |
| 5 | **4×** to the end. | "End of day 14: for people 1–30 days late, Kairos switched WhatsApp → SMS. Nobody told it." | Green callout: 13.5 → 9.7 in 100; SMS 0.27 vs 0.24 replies per ₹. | 1:15 to 1:35 |
| 6 | **Technical**, click a line under "Moves, live". | "Every move names the K27 rule and the inputs it read." | Rule card (e.g. RISK.DPD_B3_FLOOR: band in [B3, B4] → True). | 1:41 to 1:47 |

Deep links (open paused): `?at=6.05` drift · `?at=7.5` day 8 · `?at=11.98` estimates down · `?at=13.98` the
switch · `?at=14` end; add `&tech=1`, `&inspect=1`, `&autoplay=1&speed=2`. Keys: Space, →, R, 1/2/4, T, P.
**If it fails:** if the local server is not running, play R7.

## R8 · The frontend (Control Room) on the live API

**URL:** `https://dripston.github.io/kairos/control`. Check that the top right says **API live · policy
v1.1.1**; if it says offline, warm the backend's `/health` and reload.
**Fallback:** `R08_frontend_tour.mp4` (1:26, LIVE, **cut**: three passages that showed the voice vendor's name were removed whole, so the clock jumps there)

| # | Do | Say | Fallback at |
|---|---|---|---|
| 1 | Control Room · Overview; scroll. | "Aladdin runs collections on its own. Money collected vs fixed cadence, same budget and caps, zero cap breaches." | 0:00 to 0:15 |
| 2 | **Needs you** → **Approve** on the approval item. | "It stops and asks when a person is needed." (This queue is the page's own simulation, not the API.) | 0:15 |
| 3 | **Live activity** tab. | "Every event in plain words." | 0:22 |
| 4 | **Smart CRM** → a borrower (e.g. BRW-SL100001). | "Next action and why, consent per channel, EMIs, every touch from the ledger." Panels marked SIMULATED · API are from the backend. | 0:32 to 0:42 |
| 5 | **Explain**. | "AI words the recorded facts; it never decides." | 0:46 |
| 6 | Manual send · **Preview**. | "It renders the template and runs the Gate without reserving; a manual send needs a second person." | not in the recording (cut) |
| 7 | **Cohorts** → Preview match; **Campaigns**; **Voice bots** → Pick for BRW-SL100001. | | 0:54 to 1:13 (Voice bots cut) |
| 8 | **Learning & Proof** (scroll). | "What the learner believes per cohort, channel, region; replay evidence." | 1:13 |
| 9 | **Live journey** → **Run the journey**. | "The backend runs its scripted 10-step journey and the page plays it." | not in the recording (cut) |

**If it fails:**
- *"API offline".* Render is asleep: open `/health`, wait, and reload. Or add `?api=none` to run fully
  offline on demo data, and say so.
- *Avoid BRW-SL100033.* Testing left a supervisor hold on it.

## R9 · CM-03 console

**URL:** `https://kairos-backend-5ifc.onrender.com/cm03/console`
**Fallback:** `R09_cm03_console.mp4` (1:44, **LOCAL**: same code at 997260d with no model key, on a
fresh database). It is LOCAL because the deployed demo phones were busy (see the warning below).

| # | Do | Say | You should see | Fallback at |
|---|---|---|---|---|
| 1 | Pick a phone, click **New chat**, type **hi**, **Send**. | "Nothing is shared before verification." | The bot sends a 6-digit code to the registered number; the code shows under "Simulated phone". | 0:07 to 0:18 |
| 2 | Type the code. | | "Thank you, you are verified. How can I help? 1 Balance…" | 0:21 to 0:25 |
| 3 | **kitna baaki hai mera?** | "Hinglish works." | Balance per loan (B-1042 has 2 loans). | 0:29 to 0:34 |
| 4 | **salary late aayi hai, agle hafte pakka bhar dunga**, then **25 Nov**. | "A promise to pay, with a date within 7 days." | "…noted your commitment to pay by 25 Nov 2026. We will pause reminders until then." | 0:38 to 0:53 |
| 5 | **Pay link**. | | A signed link, valid 24 h. | 0:58 to 1:00 |
| 6 | **OTP scam**. | "Social engineering is caught." | "For your security, I can't help with this request in chat… never asks for your OTP"; state HANDOFF, fraud suspect YES. | 1:04 to 1:07 |
| 7 | **Injection** or **Edited screenshot**. | "While a person owns the case, the bot only sends a holding reply." | "Thank you. A member of our team has your request…" | 1:12 to 1:27 |
| 8 | **STOP**. | "STOP is honoured on every channel." | "…You will not receive further automated reminders from us on any channel." | 1:32 to 1:34 |

**Warning:** after a fraud or security hand-off, that phone's conversation stays with a person for up to
30 minutes. **New chat only clears the screen.** Every message, even "hi", then gets the holding reply.
To show the injection or the edited screenshot as the *first* trigger, use a phone that has not been
used in the last 30 minutes. On the deployment, B-1042 was left in HANDOFF + STOP by these recordings at
04:36 IST, and B-2001 was in use by testing. Render restarts reset this; otherwise pick the phone least
recently used. For the inbound story, Journey 2 (`/demo/journey/inbound`, with **Reset demo**) is the
safer live path.

## R10 · Proof in a terminal

**Where:** a terminal in `kairos-main/backend` with the venv active. pytest takes about 1 min 45 s, so
run it before you go on stage, or show the recording.
**Fallback:** `R10_proof_terminal.mp4` (1:38, LOCAL; the pytest part is shown at 4× and badged)

| Command | Expect | Fallback at |
|---|---|---|
| `python scripts/run_redteam.py` | `49 attacks: 49 PASS, 0 FAIL (0 known findings), 0 ERROR, 0 SKIP` (about 9 s) | 0:04 to 0:14 |
| `PYTHONPATH=. python scripts/bench_ledger.py 50000` | `50,000 atomic decisions in 11.2s (4,471/s)` · cap breaches 0 · `target: 50,000 in < 600s -> PASS` | 0:18 to 0:37 |
| `python -m pytest` (pyproject already adds `-q`; `-q` again suppresses the summary) | `3094 passed, 1 skipped in 103.60s` | 0:41 to 1:09 |
| `python scripts/ope_k09.py --quick` | Table: Greedy K09 vs CN10 fixed calendar (DR 33.1 vs 18.5 replies per 1,000), 95 % CIs | 1:13 to 1:20 |
| `python scripts/calibrate_thresholds.py --quick` | `MODEL_ACCEPT hand-set 0.70 -> calibrated 0.50 (certified…)`; STOP / HARDSHIP recall 1.0 | 1:25 to 1:28 |

`bench_ledger.py` needs `PYTHONPATH=.` (without it: `ModuleNotFoundError: kairos_backend`). OPE and
calibration write reports into `backend/reports/`.

---

## After the demo

- Outbound journey: if you paused the campaign or pressed PAUSE ALL OUTBOUND, resume both. The session is
  throwaway; the next Start replaces it.
- Inbound journey: click **Reset demo**.
- `/demo/live`: leave it finished, or press Stop.
