# Kairos film: product truth table

Claim → human benefit → visible proof (scene) → evidence / source → qualification → status.
Sources are on `origin/main` unless noted. **Every number is SIMULATED** (synthetic borrowers, the DEV
simulator, scripted conversations). Only `VERIFIED` rows are spoken or shown as fact. `ILLUSTRATIVE` rows are
labelled in frame (or are plainly a picture of the problem, not a product claim). Scene ids are those in
`shot-list.csv` (n01–n17).

**Primary promise:** the right message, at the right time, or none at all.

**Core proof points (three):**
1. More back per rupee: about 2× money recovered per ₹ spent, compared with a fixed schedule (n15).
2. Safe by design: Aladdin checks every message twice; no messages at night; never over the limit; STOP stops
   every channel; identity before details; fake proofs caught; 0 rule breaches, 49/49, 148/148 (n08, n09, n12, n16).
3. It runs itself, with people in charge: it decides per borrower, answers in seconds, learns, and the team only
   approves the few decisions that need a human (n05–n07, n10–n11, n13, n14).

## Claims in the film

| Claim (as said / shown) | Human benefit | Visible proof | Evidence / source | Qualification | Status |
|---|---|---|---|---|---|
| "About twice as much money for every rupee spent, compared to a fixed schedule" (~2×) | More recovered for the same spend | n15 hero number + fine print "517 vs 272 · 530 vs 229 · 459 vs 268 (1.7× to 2.3×)" | `backend/README.md` "Final unseen seeds, run once": ₹ recovered per ₹ spent CN10 / Kairos 272 / 517, 229 / 530, 268 / 459 | SIMULATED; 3 seeds never used for tuning; same budget cap and contact caps; Kairos spent less of the budget (≈ 1,650–1,776 vs ≈ 2,836–2,973), which is part of the ratio; "In our tests" on screen and in VO; fine print says it | VERIFIED (SIMULATED) |
| "With zero rule breaches" (0) | No consent, quiet-hours, cap or budget violations | n16 "0"; n14 rule-breach card from `/demo/live` | Same section: "K01 breaches and K15 violations were 0 / 0 everywhere"; PROBLEM_STATEMENT_CHECKLIST row 11 | SIMULATED replay | VERIFIED (SIMULATED) |
| "Every safety test passed": 49/49 attack tests passed | Guardrails hold under attack | n16 | INTEGRATION.md "Red team: 49/49 PASS" (PRs #14, #16, #19) | Red-team suite in repo | VERIFIED |
| 148/148 policy checks | Kairos follows the lender's written rules | n16 | `POLICY_COVERAGE.md` (Ojas policy pack, PR #25): K01 decision policy 38/38, K10 inbound WhatsApp 33/33, K13 exceptions 25/25, K21 propensity/intensity 20/20, K24 portfolio intake 32/32 | **K10 passes only with `identity_mode="k10"`; the CM-03 default stays OTP (open decision K10-1).** K21 scorecard is not yet wired into the Supervisor (K21-1). K24 script prints 32 PASS lines while its summary says 31/31 | VERIFIED (with caveat) |
| "Aladdin, our safety supervisor, checks it against the lender's rules. Twice." | Nothing goes out by mistake | n08 two checklists (when planning / just before sending) | CLAUDE.md "Guardrails run twice: at approval (reserve) and at send time"; journey report "Gate re-run said SEND ELIGIBLE" | Aladdin = Kairos Supervisor (K06), a display name; code unchanged | VERIFIED |
| "No messages at night. Never more than allowed." | Respect | n09 blocked cards "10:00 pm reminder", "4th message this week" | K06 quiet-hours and contact-cap checks; red-team suite | Card wording illustrative of the two rules | VERIFIED |
| "If someone says stop, everything stops. On every channel." | Respect | n09 "ALL OFF", channel rows switch off; real CM-03 console STOP reply (`cm03_stop.jpg`) | Checklist CM-02 #3 (red-team CON-01, CON-02); CM-03 STOP_HONOURED | From the next cycle | VERIFIED |
| Looks at each borrower: how late, how they replied, when they read (42 days; Twice, on WhatsApp; Responsive on WhatsApp) | It treats people as people | n05 real CRM page B-1042 (`crm_borrower.jpg`) | CRM replay on `origin/main` | Synthetic borrower | VERIFIED (SIMULATED) |
| "Then it decides. Contact them today, or wait." | No pointless messages | n06 | K09 contact-or-wait decision; replay "WAIT until 21 Nov" in the CRM screen | | VERIFIED |
| Picks the channel (SMS, WhatsApp, voice call in their own language, human agent) and the best time of day (7:30 pm) | The message lands | n07 | K09 channel and slot choice; 6 Sarvam voice bots, 8 Indian languages (CLAUDE.md); human hand-off | 7:30 pm is an example slot | VERIFIED |
| "Kairos answers in seconds, in Hindi or in English" | Borrowers get help when they ask | n10–n11 chat (generic UI) | CM-03 WhatsApp agent replies in well under a second (the real console in `cm03_stop.jpg` prints the engine reply time and a 16 ms round trip); Hindi and English replies in the CM-03 eval | Excludes WhatsApp network time; chat is a rebuilt UI, the Hindi line is spoken by Neha | VERIFIED (SIMULATED) |
| "It confirms who they are before sharing any loan details" | Privacy | n12 chat: one-time code → "✓ Identity verified" → ask for UTR | CM-03 eval: 0 replies with account tokens before VERIFIED | OTP mode (the CM-03 default) | VERIFIED (SIMULATED) |
| "If someone sends a fake payment screenshot, it checks with the bank, and catches it" | No one is cleared on a doctored screenshot | n12 real CM-03 console (FRAUD_SUSPECT, `cm03_fake_caught.jpg`), "No matching payment", "FAKE CAUGHT" | `backend/README.md` CM-03 eval: 127 / 127 fakes caught, 0 false positives on 873 genuine; POLICY_COVERAGE K15-T06 | Scripted eval (1,000 conversations) | VERIFIED (SIMULATED) |
| "Every reply, and every payment, teaches Kairos something new" | Gets better without a person | n13 learning panel; "Day 7: WhatsApp replies drop" | K09 replay "In-campaign channel switches"; per-channel reply estimates update daily | Curve and bar lengths illustrative; labelled "PRODUCT VIEW … SIMULATED" | VERIFIED (concept) / ILLUSTRATIVE (chart) |
| The team watches the money come in and approves the few decisions that need a human | People stay in charge | n14 ₹ recovered (real `/demo/live` crops), approval on the phone | HITL approvals (K13, hardship to a person); `/demo/live` on branch `demo/live-collections` | Rebuilt product view with real crops; labelled "REAL NUMBERS … SIMULATED" | VERIFIED (SIMULATED) |
| Same message, same time, to everyone; most ignored; some annoy; each costs money (₹ counter) | The problem | n02 three identical lock screens; n03 b-roll; cost chip | General industry picture, not a Kairos claim | Cost counter labelled "illustrative" in frame | ILLUSTRATIVE |

Claims from earlier cuts that are **not** in this film: +80–105% replies per rupee, more verified payments per
seed, 73.2% chats without a human, 500 conversations at once, 95% understanding, 50,000 decisions in about
2 minutes, the cricket scoreboard. Their sources are unchanged in the repo history of this file.

## Real screens used (captured 19 Nov 2026, synthetic data)

| File | What | How |
|---|---|---|
| `assets/real/crm_borrower.jpg` | CRM borrower page B-1042 after the 5-step replay (n05) | `cd crm && python3 -m kairos.web` + `python3 -m kairos.replay`, headless Chrome 1920×1080, `origin/main` 160fb45 |
| `assets/real/cm03_stop.jpg` | CM-03 console: STOP honoured on every channel (n09) | `KAIROS_CM03_DEMO_INBOX=1 uvicorn kairos_backend.api:app`, conversation played in the browser |
| `assets/real/cm03_fake_caught.jpg` | CM-03 console: OTP, edited payment screenshot, FRAUD_SUSPECT hand-off (n12) | same |
| `assets/real/live_recovered_1-4.png`, `live_breaches.png` | ₹ recovered card and rule-breach card (n14) | `/demo/live` on branch `demo/live-collections`, headless Chrome at 2×; only credible element crops (the mid-run ratio tiles were left out) |
| `assets/real/cm03_verified_paid.jpg`, `journey_report.jpg`, `api_docs.jpg` | Kept for reference, not in this cut | as above |

Phone numbers in these screens are masked by the product (`+********2001`). Demo OTP codes are one-time
test codes from the simulated inbox, not credentials. "Ravi" in the chat is a synthetic name.
