# Kairos: 2-minute explainer film (script, first cut)

**Runtime:** 2:00 (120 s), 9 scenes. **Voice-over:** about 270 words, plain English, warm and confident.
**Audience:** judges, the room, a lender's operations head. No jargon.
**Honesty rule:** every borrower is synthetic ("Ravi" is invented), every number is SIMULATED, and the
proof scene says so on screen.

The motion-graphics version of this script is `kairos_film.html` (same timecodes, same lines as
captions). The live-action / screen-capture version is in `SHOT_LIST.md`. All numbers live in the
`CONFIG` block at the top of `kairos_film.html`; if a number changes, change it there and here.

---

## 1. The problem (0:00 to 0:13)

**Voice-over:**
> Meet Ravi. His EMI is a few days late. This morning he got the same reminder for the fifth time.
> He stopped reading after the second. And the lender paid for all five.

**On screen:** "Meet Ravi." · "5 identical reminders today" · "Replies: 0" · "Lender's budget, spent on every one"

**Visual:** a phone on a lock screen. The same notification ("Your EMI is overdue. Please pay now.")
lands five times; the phone buzzes each time and the cards stack up. Beside it a counter climbs
1 to 5 and a budget bar drains, a rupee coin falling off it with every message. "Seen · no reply"
appears under the stack.

**Real product screen to record:** none (this is the "before" world). Optionally a real SMS arriving
on the team phone from the phone-demo mode (Demo E), shown five times.

---

## 2. Why it happens (0:13 to 0:24)

**Voice-over:**
> Most collections still run on a fixed calendar: the same message, to everyone, on every channel.
> It costs a lot, annoys people, and mostly goes unanswered.

**On screen:** "A fixed calendar." · "Same message. Everyone. Every channel." · chips "Costly", "Annoying", "Mostly ignored"

**Visual:** a 10-day calendar grid with four rows (SMS, WhatsApp, Email, Voice call). A playhead
sweeps across; every scheduled message fires on its fixed day and almost all turn grey ("no reply").
Two lonely green ticks.

**Real product screen to record:** the CN 10-day cadence column in the K09 replay output (Demo A),
or the Campaigns page in the demo UI showing the fixed-cadence arm.

---

## 3. Kairos decides (0:24 to 0:38)

**Voice-over:**
> Kairos changes that. Every day, for every borrower, it first asks: contact, or wait?
> And if it's contact, it picks the channel, the time and the message most likely to get a reply.

**On screen:** "Every borrower. Every day." · "First question: contact, or wait?" · decision:
"WhatsApp · 7:30 pm · gentle check-in, in Hindi" · "Checked twice: consent, quiet hours, daily cap, budget"

**Visual:** a field of dots (each a borrower) flickering teal (contact today) or grey (wait). Ravi's
card: EMI 6 days late, read the SMS but no reply, usually free in the evening, prefers Hindi.
"Contact" and "Wait" light up in turn and settle on Contact. Three columns (Channel, Time, Message)
scan and settle on WhatsApp, Evening, Gentle check-in. A final decision bar slides in with the
safety checks.

**Real product screen to record:** Control Room → the live decision feed (one decision row expanded),
and the Smart CRM borrower page "Next action" card.

---

## 4. One borrower's journey (0:38 to 1:00)

**Voice-over:**
> Ravi read an SMS but didn't reply. So Kairos tried WhatsApp in the evening, when he's usually free.
> He wrote back: "need time". Our voice bot called him in Hindi, agreed a date, and read it back.
> Reminders paused. When his payment was verified, only that loan's reminders stopped.

**On screen (timeline, right):** 1 SMS read, no reply · 2 WhatsApp in the evening · 3 Ravi: "need time" ·
4 Voice bot agrees a date and reads it back · 5 Reminders pause · 6 Payment verified: only that loan's reminders cancel

**Visual:** a phone on the left changes app with each step: SMS (Read · no reply), WhatsApp at 7:32 pm
(Ravi: "need time, salary late hai"), a voice call with the Ritu voice and a moving waveform, with the
read-back in Hindi:
> Bot: "रवि जी, क्या आप 27 तारीख तक भुगतान कर पाएँगे?" (Ravi ji, can you pay by the 27th?)
> Ravi: "हाँ, 27 को कर दूँगा।" (Yes, I'll pay on the 27th.)
> Bot: "ठीक है, 27 तारीख। धन्यवाद!" (Okay, the 27th. Thank you!)

Then a list of queued reminders all marked "paused until the 27th", then "Payment verified · Loan A":
Loan A's reminders are struck through ("cancelled") while Loan B's reminder stays "still scheduled".

**Real product screen to record:** demo UI → Live journey → "Run the journey"; the CRM borrower page
B-1042 → "Replay journey" (Demo B) for the Loan A cleared / Loan B open moment; Sarvam console test
chat with the PTP Follow-up bot (Ritu) for the date read-back.

---

## 5. Safety, three side-beats (1:00 to 1:13)

**Voice-over:**
> Safety comes first. Reply STOP on one channel, and every channel stops. A fake payment screenshot
> is caught. Lost your job? A person takes over, not a bot.

**On screen:** three cards: "STOP means stop. Everywhere." · "Fake payment proof? Caught." ·
"Hardship goes to a person." Footer: "Nothing is shared until identity is confirmed with a one-time code."

**Visual:** card 1, an SMS "STOP" and four channel chips switching off one after another. Card 2, a
payment screenshot is scanned and stamped "No matching bank record · flagged". Card 3, a WhatsApp
message "Meri naukri chali gayi" ("I lost my job") hands over to a human officer, reminders paused.

**Real product screen to record:** CM-03 WhatsApp console (OTP step, then a fake UTR caught);
red-team output line for the STOP attacks (CON-01, CON-02); CRM borrower page showing
"HANDOFF to a supervisor case".

---

## 6. People stay in charge (1:13 to 1:20)

**Voice-over:**
> And the lender's team can approve, hold or override anything. Every step is on record.

**On screen:** "People stay in charge." · approval card · audit log lines

**Visual:** an approval card ("Voice calls for 1,240 borrowers, 31 to 60 days late") with Approve /
Hold / Override. A cursor clicks Approve; the card turns green and two audit lines appear.

**Real product screen to record:** Control Room → Approvals panel; click Approve with the
role switch on "supervisor"; then the audit list.

---

## 7. It learns (1:20 to 1:38)

**Voice-over:**
> Kairos learns from every reply. Midway through a campaign, WhatsApp starts working less well.
> Kairos notices, and switches on its own. Borrowers move from cheap messages, to WhatsApp, to a call,
> to a person, and it learns what works in each region, for each borrower.

**On screen:** "It learns from every reply." · chart "Replies per message, by channel" · callout
"WhatsApp got worse → Kairos switched by itself" · "Where Kairos sends" bar · four layers
(SMS & email, WhatsApp, Voice bot, Person) · chips "Per region", "Per borrower"

**Visual:** three channel lines draw across 14 days; the WhatsApp line drops at day 7 and a marker
appears. The "Where Kairos sends" bar shifts away from WhatsApp towards SMS and voice. On the right,
dots (borrowers) move between four layers: up when they don't reply, back down when they reply or pay.
Chips: "Region A: evenings work best", "Region B: voice calls in Tamil", "Ravi: WhatsApp after 7 pm"
(illustrative).

**Real product screen to record:** Learning page (bars moving); K09 replay output line
"In-campaign channel switches" (Demo A); the auto-churn cohort and layer view (K27).

---

## 8. Proof (1:38 to 1:53)

**Voice-over:**
> Against the lender's own schedule, on data it had never seen, with the same budget: around fifty
> percent more replies per rupee, and zero rule breaches. Fifty thousand decisions, in about two minutes.

**On screen:** "Does it work?" · "SIMULATED data" label · "~50% more replies per rupee than the
lender's own 10-day schedule" (bars: lender's schedule vs Kairos) · "0 rule breaches" ·
tiles "50,000 decisions in about 2 minutes, same input, same result" · "49/49 attack tests passed" ·
"73% of WhatsApp chats resolved without a human" · "127/127 fake payment proofs caught" ·
chips "95% message understanding (Hinglish, Hindi, English)", "6 voice bots · 8 Indian languages",
"500 WhatsApp conversations at once" · footnote "1,500 synthetic borrowers × 14 days, 3 unseen
seeds (+48% / +61% / +56%), same budget and caps"

**Visual:** numbers count up; the Kairos bar grows past the lender's bar.

**Real product screen to record:** terminal running Demo A (the replies-per-₹1,000 lines and
"K01 cap breaches: 0 / 0"); Demo G red-team summary; `run_cm03_eval.py` summary.

---

## 9. Close (1:53 to 2:00)

**Voice-over:**
> Kairos. Right message. Right time. Or none at all.

**On screen:** "Kairos." · "Right message. Right time." · "Or none at all." ·
"The A Team · CreditNirvana Agentathon 2026" · "Synthetic borrowers · SIMULATED results"

**Visual:** the wordmark settles on a soft teal glow; lines appear one by one.

**Real product screen to record:** none; end on the title card.

---

## Word count and pacing

| Scene | Length | Words |
|---|---|---|
| 1 The problem | 13 s | 32 |
| 2 Why it happens | 11 s | 26 |
| 3 Kairos decides | 14 s | 34 |
| 4 Ravi's journey | 22 s | 49 |
| 5 Safety | 13 s | 28 |
| 6 People in charge | 7 s | 15 |
| 7 It learns | 18 s | 46 |
| 8 Proof | 15 s | 32 |
| 9 Close | 7 s | 9 |
| **Total** | **120 s** | **about 270** |

About 2.3 words a second, which leaves room to breathe at the start of each scene.
