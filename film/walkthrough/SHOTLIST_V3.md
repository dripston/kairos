# Kairos films v3: human-story commercials with the real product inside

Status: draft for the user, 20 Nov, about 03:15 IST. This replaces the walkthrough-only grammar of rev 2 and rev 3.

## Grammar

1. **Real people carry the story.** The cast is Indian, the grade is warm with slight contrast, and the same grade is used on every clip.
2. **The product appears where those people see it.**
   - The real UI is composited onto the screen in the footage, through a perspective-correct homography. Green phones are chroma-keyed first, and the screen corners are tracked per frame.
   - Then the camera pushes into the screen until the real component fills the frame: one component, sharp, on a cream canvas.
   - There is no blurred page behind it, no floating cards, and no overlay on UI text.
3. **The rhythm:** human shot (2–4 s) → push into their screen → real product moment (3–6 s) → back to the human reaction.
4. **Text has its own place.**
   - Headlines appear only over clean footage or on their own cream card.
   - Closed captions sit in a reserved band at the bottom: white on a dark translucent pill, ≥ 46 px, ≤ 2 lines, about 42 characters per line.
   - All framing keeps that band clear, and a QA check fails if any spoken second has no caption.
   - `.srt` and `.vtt` sidecars are delivered for both films.
5. **Voices.**
   - **Neha** narrates, in sparse lines.
   - **Characters speak in two voices**: borrower = Pooja, agent = Ritu (Sarvam bulbul:v3).
   - Captions carry speaker tags, e.g. "Sunita:", "Kairos agent:", "Asha:".
   - Where the phone shows the real engine's English text, the voice line is regenerated to say exactly that text. Phone, voice and caption always match. The old `wa_a2` line says ₹4,250, but the real chat says ₹5,000, so it gets re-voiced.
6. **Music:** the same licensed tracks and sound design, which the user liked. Edits stay on bar lines; there is a hard stop before each lockup.
7. **Facts are unchanged:**
   - SIMULATED labels on everything simulated.
   - ≈2× is from 3 unseen seeds, vs a fixed-schedule stand-in.
   - 49/49 is "on our own red-team suite".
   - Only verified citations are used.

**Product sources**, all real and all recorded headless (no screen recording):
- The **deployed dashboard**: https://dripston.github.io/kairos/ (Control Room, Smart CRM, Learning & Proof, Voice bots, WhatsApp console). It is live on `kairos-backend-5ifc.onrender.com` ("API live · policy v1.1.1", checked 03:05).
  - Recorded in **light mode** for the laptop composites, with read-only and normal demo flows only.
  - No kill switch, and no STOP on hero borrowers.
- The two **journey pages**: Vikram / Aarav outbound, and Borrower A / B inbound.
- The real **terminal runs**.

H-numbers are the footage agent's shots H1–H13 (`film/assets/human/`). Until they land, the existing Pexels clips stand in.

---

## Film A: "Kairos in action" (about 2:00)

**Music:** AtlasAudio, 0 → about 120 s (it runs into the quiet outro for results and night), then a hard stop, then the lockup.

| # | ≈Time | Shot | Real product inside | Voice (captioned) | On screen (clean areas only) |
|---|---|---|---|---|---|
| 1 | 0:00–0:04 | **H1** Indian city morning: street, scooters, chai stall | — | — | — |
| 2 | 0:04–0:09 | **H2 Vikram** on a scooter / at his small shop. The phone buzzes; close-up on the **green phone** | **Composite:** the same reminder three times, stacked (the real CN10 fixed-cadence template text from the repo) | **N1** "Every month, lenders send the same reminder, at the same time, to everyone." | — |
| 3 | 0:09–0:11 | Vikram swipes it away and sighs | — | — | — |
| 4 | 0:11–0:13 | **H3** another borrower, worried, looking at a phone at night | — | — | — |
| 5 | 0:13–0:19 | **H4 Asha**, ops lead, at the office: spreadsheet, frowning | — | — | Over the clean wall/sky part of the frame: unit chart **1,262 messages → 23 replies**, tag "Fixed schedule · SIMULATED run, seed 42" |
| 6 | 0:19–0:23 | Cream card | — | **N2** "Kairos looks at every borrower, every day, and decides." | Logo + "Collections that decide who, when and how." |
| 7 | 0:23–0:30 | **H5 Asha** opens her laptop. **Push into the screen** | **Composite → full frame:** the deployed Control Room (light): "API live", money collected vs fixed cadence, cap breaches 0 | — | — |
| 8 | 0:30–0:36 | Full-frame product: one card | Vikram's decision card (journey page): "Vikram gets an SMS at 9:30 am …" | — | — |
| 9 | 0:36–0:40 | Full-frame product | Aladdin's check card (every rule passed, all channels) | **N3** "Before anything goes out, Aladdin, our safety supervisor, checks it against the lender's rules." | — |
| 10 | 0:40–0:44 | Full-frame product | "No reply → learner update: SMS 8.0 → 4.6 in 100" → pipeline "Contact: WhatsApp at 09:30" | **N4** "No reply on SMS. So it learns, and tries WhatsApp." | — |
| 11 | 0:44–0:50 | **H6 Vikram**, evening, on the sofa. The phone buzzes; green phone | **Composite:** the WhatsApp reminder (real approved K22 WhatsApp template text), then he types "मैं 29 तारीख तक भुगतान कर दूंगा" (the real reply in the run) | (no voice: text only) | Caption: *"Vikram: I'll pay by the 29th."* |
| 12 | 0:50–0:53 | Full-frame product | Intent card: "ptp · rules 0.92 → ptp hold" | — | — |
| 13 | 0:53–0:57 | **H7** a UPI payment at a shop counter: QR scan, phone tick | — | — | — |
| 14 | 0:57–1:02 | **H4 Asha's laptop**, push in | "Vikram paid ₹7,409, confirmed by the bank. Kairos spent ₹0.65." + PAID · EXITED card | — | — |
| 15 | 1:02–1:09 | Asha clicks: Aarav's held voice-bot call → **Approve**. Then she tries **SMS at 20:00** | Spotlight → **blocked: outside the allowed contact hours** (full-frame card) | **N5** "Your team stays in charge." **N6** "But even they can't break the rules." | then a cream card: **"Blocked. Even for you."** |
| 16 | 1:09–1:13 | **H8 Sunita** at home, picks up her **green phone** | — | — | — |
| 17 | 1:13–1:22 | Sunita's phone (composite), then a push-in to the real inbound chat | Real inbound chat: "When is my EMI due?" → code request → code → verified → pay link | **Sunita:** "When is my EMI due?" **Kairos agent:** "First, a 6-digit code to your registered number…" (re-voiced to match the real text) | — |
| 18 | 1:22–1:26 | Full-frame product | Engine card: "Identity first: a code went to the registered number. Nothing about the account was shared." | **N7** "Kairos answers in seconds. But only once it knows it's really them." | — |
| 19 | 1:26–1:33 | Sunita sends a payment screenshot (composite) → full-frame engine card **"Fake proof caught"** | the real doctored-screenshot verdict | **Sunita:** "I've paid, here's the screenshot." **Kairos agent:** "…a specialist from our team will review it." (from the real reply) | — |
| 20 | 1:33–1:41 | **H9 support agent with a headset** (real UI's agent: Asha). Clicks **Take over** → types | Real agent console: "You own it · bot silent" → her reply lands on the borrower's phone | **Sunita:** "I lost my job, I can't pay." **Agent:** "I'm sorry to hear that. I've paused all reminders for 30 days…" (the real sent text) | — |
| 21 | 1:41–1:47 | Sunita types STOP → full frame | "STOP honoured on every channel: WhatsApp, SMS, voice and email. 4 queued reminders cancelled." | **Sunita:** "Please stop messaging me." **N8** "And stop means stop. Everywhere." | — |
| 22 | 1:47–1:55 | **H10 Asha's team** around the laptop, nodding | Composite: the results screen (Control Room "Cap breaches 0") → cream proof card | **N9** "In our tests, about twice the money back for every rupee. With zero rule breaches." | Proof card: ≈2× · 0 · 49/49 (SIMULATED, 3 unseen seeds, fixed-schedule stand-in · our own red-team suite) |
| 23 | 1:55–1:58 | **H11** night city | — | — | — |
| 24 | 1:58–2:03 | Hard stop → lockup | — | (silence) | Logo · "The right message, at the right time. Or none at all." · credits |

Neha: 9 lines (N1–N9). Character lines: about 7, voiced by Pooja and Ritu. All are captioned.

---

## Film B: "How Kairos works" (about 2:00)

**Music:** The_Mountain, 0 → 108 s (bar 36), then a hard stop and the lockup, as now. Extend to bar 40 (120 s) if the human beats need the room.

**Grammar change:**
- The diagram is no longer the main frame. It becomes a **"you are here" mini-map**: about 420 px wide, top-right, with the active node lit.
- The full diagram appears only twice: at the start (the parts are named) and at the end (one pulse end to end).

| # | ≈Time | Shot | Real thing inside | Voice (captioned) |
|---|---|---|---|---|
| 1 | 0:00–0:06 | **H12/H13** engineers at monitors; Asha walks in | — | **T1** "This is what happens inside Kairos, for one borrower, on one day." + headline over clean footage: **"How Kairos works."** |
| 2 | 0:06–0:12 | Full diagram, built in 3 s (cream) | — | — |
| 3 | 0:12–0:20 | **H1** borrowers on the street → mini-map K03 → full-frame Vikram's record card | real record (EMI ₹7,409, 10 days, consents) | **T2** "Everything starts from one record…" |
| 4 | 0:20–0:30 | **H6** Vikram's phone at evening → mini-map K27/K09 → learner posterior table → raw decision JSON | real | **T3** "Each day, a Bayesian learner scores every channel and hour, per segment and per borrower." |
| 5 | 0:30–0:42 | **H4** Asha's override at 20:00 → mini-map K06 → blocked card → raw JSON `rule_order` | real | **T4** "Models only suggest. Aladdin reserves the budget and limits, then checks every rule again at send time." |
| 6 | 0:42–0:52 | **phone notification close-up** → mini-map executor / channels / webhooks → system-trace cluster with live counters | real | **T5** "Messages go out through each channel. Receipts come back as webhooks…" |
| 7 | 0:52–1:02 | **H9 support agent with a headset**, a voice-bot call | deployed **Voice bots** page: calls with disposition, confidence and the rule that fired | Call: **Ritu (bot):** "…could you pay by Friday?" **Sunita:** "Yes, I'll pay on Friday." (existing `vc_*` lines) + **T6** "Every call returns a disposition with a confidence. Below the hand-set threshold, a person reviews it." |
| 8 | 1:02–1:12 | Intent card (rules decided, 0.92) → agent console **Take over** | real | **T7** "Replies climb a ladder: rules, then a small model. Still unsure? A person decides." |
| 9 | 1:12–1:20 | **H9** agent clicks Take over; audit row | real | **T8** "People can step in at any moment. Every action passes Aladdin, and lands in the audit log." |
| 10 | 1:20–1:42 | **H13 engineer's monitor** (composite) → push into the terminal | determinism IDENTICAL · 49 PASS / 0 FAIL · 50,000 in 11.1 s / 0 breaches · 3030 passed · OPE 18.2 vs 18.4 (SIMULATED) | **T9** "Same data in, same decisions out. And we attack it, load it, and test it." |
| 11 | 1:42–1:47 | Built on research (cream card, 10 verified citations) | — | — |
| 12 | 1:47–1:52 | Full diagram, one pulse end to end | — | — |
| 13 | 1:52–1:57 | Hard stop → lockup | — | (silence) |

---

## How it gets built (same pipeline, new modules)

**`tools/screen_comp.swift`** (Core Image), per shot:
- chroma-key the green screen
- find the 4 screen corners per frame (extreme points of the keyed region, smoothed), or use hand-set corner keys for laptops
- warp the real UI frame onto it (`CIPerspectiveTransform`), apply the grade, and write a composited clip

The compositor plays these clips and does the push-ins into the screen.

**Captions:**
- One cue per spoken line, from the mix timeline. Long lines are split by phrase.
- Burned in by the compositor, and also written to `.srt` and `.vtt`.
- `render --qa` fails if any voiced second is uncaptioned, or if the caption band overlaps a product subject.

**Dashboard recordings:** the recorder can already drive the deployed site (tested on 10 pages at 03:05). I'll record in light mode at 1440×900 for laptops, plus clean component crops for the full-frame moments.
