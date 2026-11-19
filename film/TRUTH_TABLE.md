# Kairos film: product truth table

Claim → human benefit → visible proof (scene) → evidence / source → qualification → status.
Sources are on `origin/main` unless noted. **Every number is SIMULATED** (synthetic borrowers, the DEV
simulator, scripted conversations). Only `VERIFIED` rows are on screen or spoken; `ILLUSTRATIVE` rows are
labelled as such in frame; `VERIFY` rows are not used in the film.

**Primary promise (both cuts):** the right message at the right time, or none at all. Collections run
themselves, safely, and bring back more money for every rupee.

**Core proof points (max three per cut):**

| # | Full film | Operator cut |
|---|---|---|
| 1 | More back per rupee: ~2× ₹ recovered per ₹ spent (s8, o5) | Runs itself: the day's plan is ready, the score updates live (o1, o2) |
| 2 | Safe by design: 0 rule breaches; STOP everywhere; fakes caught; Aladdin checks twice (s5, s8) | Only the big calls reach a person, and Aladdin re-checks (o3) |
| 3 | Runs itself, people in charge: 73.2% of chats closed without a human; 50,000 decisions in about 2 minutes; approvals (s6, s8) | Result: ~2× ₹ recovered per ₹ spent, 0 wickets (o5) |

## Claims

| Claim (as said / shown) | Human benefit | Visible proof | Evidence / source | Qualification | Status |
|---|---|---|---|---|---|
| ~2× money recovered for every rupee spent vs the lender's own schedule | More recovered for the same spend | s8 hero + bars 272 vs 517; o5 "Kairos won" | `backend/README.md` "Final unseen seeds, run once": ₹ recovered per ₹ spent CN10 / Kairos 272 / 517, 229 / 530, 268 / 459 (1.71× to 2.31×) | SIMULATED; 3 seeds never used for tuning; same budget cap and contact caps; Kairos spent less of the budget (≈ 1,650–1,776 vs ≈ 2,836–2,973), which is part of the ratio | VERIFIED (SIMULATED) |
| +80% to +105% more replies per rupee | Fewer wasted messages | s8 hero sub-line | Same table: replies per ₹1k +104.9%, +96.4%, +80.3% | SIMULATED; same seeds | VERIFIED (SIMULATED) |
| More verified payments on every fresh test | Real money, not just replies | s8 hero sub-line | Same table: payments 59→63, 45→69, 56→68 | SIMULATED; payments verified by the simulator's bank feed | VERIFIED (SIMULATED) |
| 0 rule breaches | No consent, quiet-hours, cap or budget violations | s8 card 2; o2 "WICKETS 0"; o5 | Same section: "K01 breaches and K15 violations were 0 / 0 everywhere"; PROBLEM_STATEMENT_CHECKLIST row 11 | SIMULATED replay | VERIFIED (SIMULATED) |
| 49 / 49 attacks blocked | Guardrails hold under attack | s8 card 2; VO | FILM_BRIEF / INTEGRATION.md "Red team: 49/49 PASS" (PRs #14, #16, #19) | Red-team suite in repo | VERIFIED |
| Fake payment proof caught (127 / 127) | No one is cleared on a doctored screenshot | s5 real CM-03 console (FRAUD_SUSPECT); s8 | `backend/README.md` CM-03 eval: 127 / 127, 0 false positives on 873 genuine; POLICY_COVERAGE K15-T06 | Scripted eval (1,000 conversations) | VERIFIED (SIMULATED) |
| 73.2% of WhatsApp chats resolved without a human | Team handles only what needs a person | s8 tile; VO "three in four" | CM-03 eval 732 / 1,000 | Depends on conversation mix; 67.3% counting only bank-confirmed payment claims (caveat in README) | VERIFIED (SIMULATED) |
| Nothing shared before the one-time code (0 disclosures) | Privacy | s5 chip; s8 footnote; real console shows OTP first | CM-03 eval: 0 replies with account tokens before VERIFIED | Independent scan in eval | VERIFIED (SIMULATED) |
| 500 conversations at once | Scales on a busy day | s8 footnote | CM-03 eval: 500 concurrent, 0 errors, 0 cross-talk | Engine/in-process HTTP; excludes WhatsApp network time | VERIFIED (SIMULATED) |
| 95% message understanding; 100% STOP and hardship recall | It never misses "stop" or "I lost my job" | s8 footnote; s4 "need time" understood | PROBLEM_STATEMENT_CHECKLIST: "95% on 160 hand-written messages, 100% STOP/hardship recall (PR #22)" | Small hand-written set | VERIFIED |
| 50,000 daily decisions in about 2 minutes; same input, same result | Runs every morning without waiting | s8 tile | Checklist row 12: about 2.1 min (402 decisions/s); `--check-determinism` identical hashes | Single laptop run | VERIFIED |
| 6 voice bots, 8 Indian languages, human handoff | Talks to people in their language | s8 tile; s4 Ritu in Hindi | CLAUDE.md voice-bot table; OPERATING_GUIDE Demo D | Sarvam bots in the CreditNirvana workspace | VERIFIED |
| STOP on one channel stops every channel | Respect | s5 card 1 (chips), real console STOP reply | Checklist CM-02 #3 (red-team CON-01, CON-02) | From the next cycle | VERIFIED |
| Hardship goes to a person | Dignity | s5 card 3; o3 approval | K13 exceptions; HARDSHIP_TRIAGE bot is human-approved only; CM-03 handoffs | | VERIFIED |
| Aladdin checks every message twice; re-checks after approval | Nothing goes out by mistake | s3 decision bar; s5; o3 | CLAUDE.md "Guardrails run twice: at approval (reserve) and at send time"; journey report "Gate re-run said SEND ELIGIBLE" | Aladdin = Kairos Supervisor (K06) | VERIFIED |
| Only a verified payment clears an EMI, and only that loan's reminders cancel | No wrong reminders after paying | s4 end; s4b real CRM (Loan A CLEARED, Loan B PENDING) | CLAUDE.md rules; CRM replay step 4 | | VERIFIED |
| WhatsApp gets worse mid-campaign and Kairos switches by itself | Adapts without a person | s7 chart (illustrative) | K09 replay "In-campaign channel switches" | Chart shape illustrative | VERIFIED (concept) / ILLUSTRATIVE (chart) |
| Borrowers move between layers (SMS → WhatsApp → voice → person) | Cheapest channel that works | s7 lanes | K27 auto-churn (`kairos_backend/churn/`) | Dot movement illustrative | ILLUSTRATIVE |
| Scoreboard ₹ collected, run rate, required rate, extras, 3,412 due, 1,986 / 1,426 split, partnerships 42 / 25 / 21 / 12 | Operator sees the day at a glance | o1, o2, o3/o4 score bug | None (no live view on main yet; `/demo/live` is on branch `demo/live-collections`, not pushed) | Labelled "illustrative / SIMULATED" in frame | ILLUSTRATIVE |
| Commentary lines (Asha rests, Kiran STOP, Ritu promise, switch to SMS, Ravi pays ₹4,200) | Every decision explained | o2 | Behaviours exist (K09 wait, STOP, K17 PTP, channel switch, verified payment); the lines are scripted | Synthetic names | ILLUSTRATIVE |
| "1,240 borrowers" approval, audit times, segment report figures, region chips | Context | s6, o4, s7 | None | Labelled illustrative where numbers appear | ILLUSTRATIVE |
| 148 / 148 of Ojas's policy cases | Policy coverage | not shown | Reported by the team; no document with this figure found on main | | VERIFY |

## Real screens used (captured 19 Nov 2026 from `origin/main` 160fb45, synthetic data)

| File | What | How |
|---|---|---|
| `assets/real/crm_borrower.jpg` | CRM borrower page B-1042 after the 5-step replay | `cd crm && python3 -m kairos.web` + `python3 -m kairos.replay`, headless Chrome 1920×1080 |
| `assets/real/cm03_fake_caught.jpg` | CM-03 console: OTP, edited payment screenshot, FRAUD_SUSPECT hand-off | `KAIROS_CM03_DEMO_INBOX=1 uvicorn kairos_backend.api:app`, conversation played in the browser, page snapshot rendered headless |
| `assets/real/cm03_verified_paid.jpg` | CM-03 console: "Kal EMI pay kar di thi" → OTP → balance → UTR → receipt | same |
| `assets/real/cm03_stop.jpg` | CM-03 console: STOP honoured on every channel | same |
| `assets/real/journey_report.jpg` | Journey report: every decision with its checks (ALL_PASSED, HITL approve + gate re-run) | `python3 scripts/run_journey.py --quick` |
| `assets/real/api_docs.jpg` | API docs "Kairos K06 · Supervisor guardrails + executor" | `/docs` |

Phone numbers in these screens are masked by the product (`+********2001`). Demo OTP codes are
one-time test codes from the simulated inbox, not credentials.
