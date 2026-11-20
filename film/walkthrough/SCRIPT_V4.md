# Kairos films v4: scripts for review (narration, on-screen text, dialogue)

Conventions
- **N** = Neha (narrator, bulbul:v3, pace 1.2). **K** = the Kairos agent, kavya at pace 1.05 (`AGENT_VOICE`). **S** = the borrower, tanya at pace 1.0 (`BORROWER_VOICE`).
- **TTS text** is generated automatically from the caption text:
  - "Aladdin" becomes "Alaadin" in TTS only; captions keep "Aladdin".
  - Hyphens and dashes become spaces or words ("6-digit" → "6 digit", "1–30" → "1 to 30", "49/49" → "49 out of 49").
  - Digits stay digits.
- **SCREEN** = on-screen text (titles and cards on cream or glass). Real UI is always a whole page in a glass window on the nebula, then a smooth zoom to the region.
- All numbers are SIMULATED (synthetic borrowers, dry-run adapters) and labelled on screen.
- Timings are targets; the TTS durations set the final cut, and the music is cut on phrases.

---

## A. PRODUCT film: "Kairos in action" (target 3:20)

It solves both problem statements, visibly: Part 1 covers outbound (CM-02), Part 2 covers inbound WhatsApp (CM-03), the team stays in control, and the film ends on the results. No jargon. No red team.

### 0:00–0:09 Cold open
- **SCREEN** (black, small legal type): "DISCLAIMER: This product may make your collections team's life noticeably easier. Side effects include fewer angry customers and more money recovered. Use at your own risk."
- 1 beat of silence, then the music hit.

### 0:09–0:16 Title
- Kairos logo reveal on the nebula.
- **SCREEN:** "Collections that decide who, when and how."

### 0:16–0:40 The problem
Montage of varied people (new faces; phones as b-roll, screens not readable). Then a static phone frame shows the real SMS, three identical copies, with a slow push.
- **N1:** "Every month, lenders send the same reminder. Same words, same time, to everyone."
- **N2:** "In one simulated campaign, a fixed schedule sent 1,262 messages. Just 23 people replied."
- **SCREEN** (unit chart): "1,262 messages. 23 replies." · tag "Fixed schedule · SIMULATED run, seed 42"
- **N3:** "Everyone else learned to ignore it."

### 0:40–1:02 Part 1 · Reaching out (CM-02)
- **SCREEN** (title card): "Part 1 · Reaching out"
- Glass window: the real Control Room (F01), whole page, then a zoom to today's sends by channel. Then Learning & Proof (F07) per segment, and the Engine Room's "Spend vs budget".
- **N4:** "Kairos looks at every borrower, every day, and makes one decision: reach out or wait. And if it reaches out, on which channel, and at what time."
- **N5:** "It keeps every campaign within budget, and reports what worked for each group of borrowers, every day."

### 1:02–1:38 Meet Aladdin (the heart)
- Flash title, full frame: "ALADDIN" / "checks every message. Twice."
- **N6:** "But a smart decision is not enough. It has to be a safe one. Meet Aladdin, our safety supervisor."
- Animated flowchart loop, built step by step, with a pulse running round it:
  - "Decide" → "Aladdin checks" → "Send" or "Block" → "Receipt" → "Reply" → "Learn" → back to "Decide"
  - The "Aladdin checks" box lists: consent · STOP · quiet hours 08:00 to 19:00 · contact limits · budget · holds
- **N7:** "Every message passes Aladdin twice: when it is planned, and again at the moment it is sent. Consent, opt outs, quiet hours, contact limits, budget and holds. If any rule says no, nothing goes out."
- The mini loop returns in a corner at each later act, with the relevant box lit.

### 1:38–2:08 Vikram (outbound)
Vikram (H7) b-roll. A static phone frame shows the real SMS. Glass window: the real outbound journey page, whole, then a zoom to each event card.
- **N8:** "Take Vikram. His EMI of ₹7,409 is overdue. Kairos sends one SMS at 9:30, Aladdin clears it, and he doesn't reply."
- **N9:** "So Kairos tries WhatsApp instead. This time Vikram answers: he will pay by the 29th."
- A static phone frame shows his real WhatsApp reply in Hindi. **Caption:** Vikram: "I'll pay by the 29th."
- **N10:** "He pays. The bank confirms it, and Kairos steps back."
- **SCREEN** (real card): "Vikram paid ₹7,409, confirmed by the bank. The journey exits. Kairos spent ₹0.65 on Vikram."

### 2:08–2:22 It keeps learning
Glass window: the Engine Room learning panel at day 7, then at the end of day 14, both whole.
- **N11:** "And it keeps learning. When WhatsApp replies dropped for one group of borrowers, nobody told Kairos. It noticed on its own, and within a week it had moved them to SMS."
- **SCREEN:** the page's own callouts ("Day 7: …WhatsApp replies drop to about a third…", "Day 14: …Kairos switched WhatsApp → SMS…") · tag "Engine Room · SIMULATED".

### 2:22–3:02 Part 2 · When borrowers write in (CM-03)
- **SCREEN** (title card): "Part 2 · When borrowers write in"
- Sunita (H4) b-roll. A static phone frame shows the real inbound chat; each turn appears as it is spoken.
- **S:** "When is my EMI due?"
- **K:** "To protect your account, I have sent a 6-digit code to your registered WhatsApp number."
- **K:** "Thank you, you are verified."
- **N12:** "Kairos answers in seconds, but only once it knows it's really her."
- **SLOT (10–15 s): real WhatsApp on a real phone.**
  - Your real recording of the phone receiving the OTP and Kairos's replies, side by side with the inbound console.
  - Tag: "Real WhatsApp · Meta Cloud API test number".
  - Until it lands: clean still screenshots of the real chat UI.
- **S:** "I already paid, here is the screenshot."
- **K:** "For your security, I can't help with this request in chat. A specialist from our team will review it."
- **N13:** "An edited screenshot, hidden instructions, a reused payment reference. Kairos spots the fake, marks nothing as paid, and passes it to a person."
- **N14:** "When another borrower writes that she has lost her job, a person takes over, and the bot goes quiet."
- **SCREEN:** glass window, the real agent console, whole: "You took over: the bot is silent" · "Hardship: hold outbound 30 days".
- **S:** "Stop."
- **K:** "You will not receive further automated reminders from us on any channel."
- **N15:** "And stop means stop. Aladdin blocks every channel, and every queued reminder is cancelled."
- **SCREEN** (real card): "STOP honoured on every channel: WhatsApp, SMS, voice and email. 4 queued reminder(s) cancelled." The mini loop shows Block lit.

### 3:02–3:22 The team stays in control
Asha (H5) b-roll. Glass windows: approvals (F02), Smart CRM manual send (F03), hold and resume (F04).
- **N16:** "Your team stays in control. They approve calls, pause and resume, and can step in at any moment. Every action they take passes Aladdin too. Even they cannot send a message at 8 pm."
- **SCREEN:** approval row "Aarav · Voice bot at 09:30" with a cursor on Approve, then the mini loop "Approve → Aladdin ✓". Then "Manual SMS at 20:00 → Aladdin ✕ Blocked: outside the allowed contact hours. No person can override this."

### 3:22–3:50 Results and ending
- Team b-roll (new faces).
- **N17:** "In our tests: about twice the money back for every rupee spent. Zero rule breaches. And 73% of chats resolved without a person, with every fake caught."
- **SCREEN** (results card):
  - "≈2× money back per ₹ · vs a fixed-schedule stand-in"
  - "0 rule breaches · every message checked twice"
  - "73% of chats resolved without a person"
  - "127/127 fake payment proofs caught"
  - Fine print: "SIMULATED · synthetic borrowers · 3 unseen seeds; 1,000 test conversations"
- Three slogans, one after another (on screen only):
  1. "The right message, at the right time. Or none at all."
  2. "Collections that decide who, when and how."
  3. "Built to be trusted with every rupee."
- Logo lockup and credits: "Music: Corporate Inspirational, AtlasAudio (Pixabay licence) · Voices: AI-generated · Footage: Pexels · Every screen is the real Kairos software · All data SIMULATED".
- Closing tag, small: "Results may vary. Rules won't."

Narration: 17 Neha lines, about 250 words. Dialogue: 7 lines (kavya at pace 1.05, tanya at pace 1.0). Sources for the results:
- PROGRESS.md CM-03: 73.2% resolved without a human (732 / 1,000 seeded conversations) and 127 / 127 fakes caught. The README caveat: counting only verified payments gives 67.3%. This goes in the QA notes.
- ≈2× per ₹ and 0 breaches: film/TRUTH_TABLE.md.

---

## B. TECHNICAL film: "How Kairos works" (target 3:00)

Grammar for each chapter: a title card ("02 · Churn engine · K27") → an animated diagram with real component names → the real system running that case (a whole page in a glass window on the nebula, Engine Room, terminal) → one or two real numbers on screen. Human footage appears only in 00 and 08.

### 00 Overview (0:00–0:16)
Engineer b-roll, then the full architecture diagram builds: K03 Record → K27 Churn → K09 Learner → K06 Aladdin → Executor → Channels → Webhooks → Intent ladder → Outcomes → back to K09; People → Aladdin.
- **N:** "Kairos is a decision engine for collections. Every day, for every borrower, it decides whether to make contact, on which channel, and when. Here is how each part works."
- **SCREEN:** "300 synthetic borrowers · 14 days · seed 42 · SIMULATED"

### 01 Data spine (K03) (0:16–0:30)
Diagram (entity data flow): Borrower → Loan → EMI obligation → Payment; Consent (per channel, versioned); Events (append-only).
Real: the Smart CRM borrower page (F03), whole page, with a zoom to the record.
- **N:** "Everything starts from one record per borrower: loans, EMIs, payments, consent for each channel, and every event. Money is kept in paise, so totals never drift."
- **SCREEN:** "₹7,409 = 740,900 paise" · "11,565 events in one 14-day run"

### 02 Churn engine (K27) (0:30–0:46)
Diagram: daily tick → four engines (cohort · risk · contact layer · lifecycle) → rule → move with its inputs.
Real: the Engine Room churn panel, whole, then the rule card for one move.
- **N:** "Each morning, the churn engine sorts every borrower again, by cohort, risk, contact layer and status. Every move records the rule that fired and the inputs it read."
- **SCREEN:** "RISK.DPD_B3_FLOOR · band in [B3, B4] → HIGH" · "547 moves in 14 days" (the count shown on the page)

### 03 Learning loop (K09) (0:46–1:06)
Diagram: touch → 24 h reply window closes → one reward → posterior per segment × channel × hour (evidence decay 0.7 per day) → decision by value per ₹.
Real: the Engine Room learning panel at day 7, then at the end of day 14.
- **N:** "The learner is an empirical Bayes model. Each touch earns exactly one reward, when its 24-hour reply window closes."
- **N:** "On day 7 we cut WhatsApp replies to a third and told it nothing. By the end of day 14, that segment had moved to SMS."
- **SCREEN:** "WhatsApp reply chance 13.5 → 9.7 in 100 · SMS now 0.27 vs 0.24 replies per ₹"

### 04 Aladdin gate (K06): reserve and re-check (1:06–1:28)
Sequence diagram: Learner → Aladdin.reserve (consent · STOP · quiet hours 08:00 to 19:00 · contact limits · budget · holds; atomic ledger reserve) → Executor (at send time) → Aladdin.recheck → Adapter, or Block → audit log.
Real: the journey Technical view of an Aladdin check card, then the manual send at 20:00 blocked (F03).
- **N:** "Models only suggest. Aladdin decides what may happen. When a message is planned, it reserves budget and contact limits in one atomic step. At send time, it checks every rule again. If anything has changed, nothing is sent."
- **SCREEN:** "50,000 atomic decisions in 11.1 s · cap breaches: 0"

### 05 Channels and webhooks (1:28–1:44)
Sequence diagram: Executor → adapter (SMS and WhatsApp dry-run; voice through AI voice agents) → receipt webhook (delivered, read = reach) → reply webhook → idempotency key → outcome finalizer.
Real: the live system trace / Live journey (F05), whole page.
- **N:** "Adapters send through each channel. Receipts and replies come back as webhooks. Duplicates are ignored, and a delivery is never counted as a reply."
- **SCREEN:** "delivered ≠ replied · 1 reward per touch" plus one counter from the page

### 06 Inbound WhatsApp (CM-03) (1:44–2:04)
State machine: NEW → OTP_SENT → VERIFIED → ANSWERED; any state → HUMAN (fraud, hardship, low confidence); STOP → OPTED_OUT (all channels).
Real: the inbound journey Technical view and the agent console, whole.
- **N:** "Inbound chats run a small state machine. Nothing about the account is shared until a one-time code is verified. Payment proof is checked for edits, hidden instructions and reused references, and a fake never marks anything paid."
- **SLOT (10–15 s):** real WhatsApp on a real phone (Meta Cloud API test number), side by side with the inbound console, if it lands. Never a composite.
- **SCREEN:** "1,000 simulated conversations: 73.2% resolved without a human · 127/127 fakes caught · 0 disclosures before OTP · first reply p95 1.6 ms engine time · 500 concurrent, 0 errors"

### 07 Voice (2:04–2:22)
Diagram: use case → registry → AI voice agent (voice, language) → call → post-call webhook (disposition, confidence) → hand-set threshold → act, or supervisor hold and review.
Real: the Voice bots page (F06), recaptured after the label fix so no vendor name shows, whole, then a zoom to the calls table.
- **N:** "Voice calls run on six AI voice agents, chosen by use case. Each call returns a disposition and a confidence. Kairos, not the bot, decides what happens next, and anything below a hand-set threshold goes to a person."
- **SCREEN:** "STOP_REQUESTED · 0.45 → K17.STOP_REQUESTED.BELOW → SUPERVISOR_HOLD_AND_REVIEW" · "thresholds 0.5 to 0.8, hand-set"

### 08 Human in the loop (2:22–2:38)
Asha b-roll. Diagram: approval window 08:00 to 09:30 · hold and resume · takeover → Aladdin → audit log.
Real: F02 approve and reject, F04 hold and resume.
- **N:** "People can approve, hold, resume or take over at any moment. Their actions pass Aladdin too, and every one lands in the audit log."
- **SCREEN:** "SMS at 20:00 → blocked · no role can override"

### 09 Determinism and testing (2:38–2:56)
Terminal (real recorded runs), with results lifted out.
- **N:** "Given the same seed, Kairos makes the same decisions every time. And we attack it, load it and test it."
- **SCREEN:**
  - "Deterministic replay: IDENTICAL"
  - "49 attacks: 49 PASS, 0 FAIL"
  - "50,000 atomic decisions in 11.1 s"
  - "3030 passed in 74 s"
  - "Off-policy evaluation (CN10): from logs 18.2, truth 18.4 · SIMULATED"

### 10 Research lineage (2:56–3:08)
The citations card (the 10 papers), then the loop pulse and the lockup "Built to be trusted with every rupee."
- **N:** "None of this is magic. Each part stands on published research."

Narration: 13 Neha lines (about 300 words). No dialogue. The word "Sarvam" appears nowhere: not in narration, labels, credits or visible UI. Frames that show it (for example "Prism (Sarvam)" on the intent card) are not used.
