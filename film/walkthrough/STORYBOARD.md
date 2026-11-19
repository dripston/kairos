# Kairos walkthrough films: storyboard and narration (rev 2)

Rev 2 applies `research/film_craft.md` (craft rules, the 12 shot moves) and `research/papers.md` (wording and
citations). Two 1920×1080 / 30 fps films, built from the **real product, really running** (local servers,
synthetic data, everything SIMULATED). There are no stock-people montages, no nebula and no mock-ups of the UI.

**Look.** A cream canvas (#FBF8F1) acts as the desk. The product sits in a clean browser window with a soft
shadow. A spring camera zooms onto the action, and a large cursor glides with click ripples. Bold short headlines
end with a period. There is one accent colour (#0F766E) in the product film; the technical film has one fixed
colour per subsystem.

## Rules both films must pass (checked in `QA.md`)

**Legibility**
- Timed text is ≥ 46 px and headlines are 72–120 px.
- Other readable text is ≥ 32 px.
- All of it sits inside 90% title-safe (a 96 px side margin).
- UI is always zoomed (×1.6–2.4, about 2×) before we expect it to be read.
- Wide shots of a whole page last ≤ 1.5 s and only set up a zoom.

**Beats**
- Each film has 12–16 beats of 5–8 s, with one idea per beat and about 3 words on screen.
- The product appears within 10 s.
- Each journey has one held "this is now possible" frame, held 1.5 s longer than feels necessary.

**Motion** (all of it is driven by a frame clock, with no CSS transitions)
- Entrances use `cubic-bezier(.23,1,.32,1)`, starting at scale ≥ 0.95.
- Moves use `cubic-bezier(.77,0,.175,1)`.
- The camera uses an overdamped spring (ζ 1.3, 90% in 317 ms), starts 0.35 s before a click, groups nearby clicks, and keeps the subject in the inner 70%.
- The cursor uses a spring of 170/20 that assists over the last 160 ms, a press dip to 0.92, and a 300 ms ripple.
- Staggers are 30–80 ms, blur is ≤ 2 px, and there is no ease-in anywhere.

**Words**
- Narration, overlay and UI never say the same sentence.
- Narration is ≤ 10 short lines per film, about 110–150 words, with voice on for about 35–45% of the runtime (Neha, bulbul:v3, pace 1.2).
- Overlays are 2–6 words.

**Sound**
- Licensed music, edited only at bar lines (no stretching), ducked 18–20 dB under the voice and back up in the gaps.
- Mastered to −14 LUFS, true peak ≤ −1 dBTP.
- Each film ends with a **hard stop into silence** (10 frames), then the logo lockup in silence.

**Claims**
- Every number is labelled SIMULATED where it appears and traces to `PROGRESS.md`, `POLICY_COVERAGE.md`, `film/TRUTH_TABLE.md` or a real run recorded for this film.
- Wording follows `papers.md` §D:
  - Say "a Bayesian learner" or "learns from every reply". Never "Thompson sampling" or "reinforcement learning".
  - Say "empirical Bayes", not "hierarchical Bayesian".
  - Thresholds are "hand-set, fail closed", not "calibrated".
  - 49/49 and 127/127 are "on our own red-team suite" or "on our seeded test set".
  - Lead with the design point: *models only label; the rule gate decides every action.*
  - ≈2× is SIMULATED, on 3 unseen seeds, against a fixed-schedule stand-in.
  - The page's 9.9× / 15.9× (seed 42 demo run) is never shown.

---

## Film A: "Kairos in action" (product film), 1:55

**Music.** "Corporate Inspirational" by AtlasAudio (Pixabay licence). 140 BPM, so a bar is 1.714 s, and the first downbeat is at 0.24 s.
- Track sections: intro 0–27.7 s, build 27.7–55.1 s, lift 55.1–110.0 s, outro from 110.0 s.
- **Edit:** play from 0 to 109.93 s, the end of the lift phrase, then **hard stop** (25 ms fade) into 10 frames of silence. The lockup follows in silence until 115.0 s. There are no internal cuts, so the music is never stretched or spliced.
- Journey 2 starts on the lift at 55.1 s.

**Protagonist.** Vikram, the outbound page's story-1 borrower; the inbound page uses its own borrower A.

| # | Time | Beat (move) | Picture: real source | Overlay | Narration |
|---|---|---|---|---|---|
| 1 | 0:00–0:06.9 | **Hook** (kinetic word stack, 4) | 0–2.0 s: Mumbai at night (aerial b-roll). 2.0–6.9 s: cream, the fixed schedule's numbers from the real seed-42 run, one at a time | **1,262 messages.** → **23 replies.**, with tag "Fixed schedule · SIMULATED run, seed 42" | **N1** "Every month, lenders send the same reminder, at the same time, to everyone." |
| 2 | 0:06.9–0:10.3 | **Promise lockup** (chapter headline, 5) | Kairos mark + wordmark. At 9.6 s the outbound journey window rises beneath it (rise) | Spec line: **Collections that decide who, when and how.** | **N2** "Kairos looks at every borrower, every day, and decides." |
| 3 | 0:10.3–0:17.1 | Journey 1 opens (cursor glide 2, auto-zoom 1) | `/demo/journey/outbound`: story picker → click **Vikram** | — (the page names the story) | — |
| 4 | 0:17.1–0:24.0 | The decision (auto-zoom 1, ×2) | Day 1: the learner's pick for Vikram (channel + hour) | **SMS. 6:30 pm.** (whatever the engine picks) | — |
| 5 | 0:24.0–0:30.8 | Aladdin checks (lift-out callout 3) | The rule checks for that message: plan-time and send-time | **Checked twice.** | **N3** "Before anything goes out, Aladdin, our safety supervisor, checks it against the lender's rules." |
| 6 | 0:30.8–0:41.1 | **Now possible #1** (cursor on "Next event", auto-zoom follows the timeline) | No reply → the learner switches to WhatsApp → "I'll pay by the 29th" → **paid ₹7,409, bank-confirmed** (held +1.5 s) | **No reply? It switches.** then **Paid. Bank-confirmed.** | **N4** "No reply on SMS. So it learns, and tries WhatsApp." |
| 7 | 0:41.1–0:48.0 | Human approves (lift-out 3, cursor 2, **match-cut through Approve** 11) | Aarav's story: a voice call held for approval → **Approve** → push through the button into the call going out | **You approve the call.** | **N5** "Your team stays in charge." |
| 8 | 0:48.0–0:54.9 | Even the team is checked (auto-zoom 1) | Operator tries **Send instead** at 20:00 → **blocked: CONTACT_WINDOW** | **Blocked. Even for you.** | **N6** "But even they can't break the rules." |
| 9 | 0:54.9–0:58.3 | **Breath** (chapter headline 5), on the music lift | Cream canvas; the inbound window rises | **Now the borrower writes first.** | — |
| 10 | 0:58.3–1:05.1 | Identity first (auto-zoom on the phone) | `/demo/journey/inbound`: "When is my EMI due?" → code request → code → answer + secure pay link | **No details before OTP.** | **N7** "Kairos answers in seconds. But only once it knows it's really them." |
| 11 | 1:05.1–1:12.0 | **Now possible #2** (split cause and effect, 9: phone ↔ engine card, both on the page) | "I already paid" + an edited screenshot → engine card **Doctored screenshot** → hand-off (held +1.5 s) | **Fake screenshot. Caught.** | **N8** "A doctored payment screenshot? It checks with the bank." |
| 12 | 1:12.0–1:20.5 | A person takes over (cursor 2, auto-zoom 1) | "I lost my job, I can't pay" → agent console **Take over** → "bot silent" → the agent's reply lands on the phone | **A person takes over.** | — |
| 13 | 1:20.5–1:27.4 | STOP (auto-zoom 1) | Borrower: **STOP** → channel chips 4/4 → 0/4, queue cleared | **STOP. Every channel.** | **N9** "And stop means stop. Everywhere." |
| 14 | 1:27.4–1:36.0 | Results (number counter 8) | `/demo/live` end of run: zoom on **Rule breaches 0** (real), then a proof card on cream with counters | **≈2×** money back per ₹ · **0** rule breaches · **49/49** attacks blocked. Fine print (32 px): "SIMULATED · 3 unseen seeds vs a fixed-schedule stand-in · on our own red-team suite" | **N10** "In our tests, about twice the money back for every rupee. With zero rule breaches." |
| 15 | 1:36.0–1:49.9 | The whole console (slow auto-zoom tour) | `/demo/live` Overview running, then the Approvals card ("Needs you") lifted; KPI tiles cropped so no ×-ratio is visible | **People decide the hard cases.** | — |
| 16 | 1:49.9–1:55.0 | **Hard stop to lockup** (12) | Music cuts; 10 frames of cream; logo + line, then credits | **The right message, at the right time. Or none at all.** Credits (32 px): music AtlasAudio / Pixabay; voice Sarvam bulbul:v3 (Neha); "All data SIMULATED" | — (silence) |

**Narration:** 10 lines, 104 words, about 40 s, so voice is on for about 35% of the runtime. The end line is on screen only. It is never spoken, so it isn't said twice.

---

## Film B: "How Kairos works" (technical film), 1:53

**Music.** "Inspiring Technology" by The_Mountain (Pixabay licence), the user's choice. 80 BPM, so a bar is 3.0 s and a 4-bar phrase is 12 s; the first downbeat is at 0.02 s.
- **Edit:** play from 0 to 108.0 s (end of bar 35), then **hard stop** into silence, with the lockup until 113.0 s.
- Beats change on phrase lines (every 12 s) or half-phrases (6 s).

**Spine: one diagram, never cleared.**
- Parts are added in flow order, one new part per beat. A part never appears before the narration names it. Earlier parts dim to 35%.
- The colour per subsystem is fixed for the whole film: data blue, learn teal, rules (Aladdin) orange, act violet, understand magenta, people green.
- Each cut-in is a **match-cut through the node** (move 11): the camera pushes into the node, the real artifact grows out of it, and on return the camera lands back on the same node.

```
 K03 Record → K27 Churn → K09 Learner → K06 Aladdin → Executor
     (blue)      (teal)       (teal)  ↑   (orange)  ↘  (violet)
                                      │      ↑ People (green)
   Outcomes ← Intent ladder ← Webhooks ← Channels
    (blue)     (magenta)      (violet)    (violet)
```

| # | Time | Diagram adds | Real artifact (cut-in) | Overlay | Narration |
|---|---|---|---|---|---|
| 1 | 0:00–0:06 | — | Chapter headline on cream | **How Kairos works.** Spec line: **One decision per borrower, per day. Every step checked.** | **T1** "This is what happens inside Kairos, for one borrower, on one day." |
| 2 | 0:06–0:18 | **K03 Record** | Outbound journey page, **Technical** view: Vikram's raw record and events | **One record per borrower.** | **T2** "Everything starts from one record: loans, payments, replies and consent. Money to the paisa." |
| 3 | 0:18–0:30 | **K27 Churn → K09 Learner** | Journey Technical: the learner's scores per channel and hour | **Learns from every reply.** | **T3** "Each day, a Bayesian learner scores every channel and hour, per segment and per borrower." |
| 4 | 0:30–0:42 | **K06 Aladdin**. Lineage caption: "≈ safety shield · Alshiekh et al. 2018" | Journey Technical: the rule ids of both passes (reserve, then the send-time re-check) | **Models suggest. Rules decide.** | **T4** "Models only suggest. Aladdin reserves the budget and limits, then checks every rule again at send time." |
| 5 | 0:42–0:54 | **Executor → Channels → Webhooks** | `/demo/live` System page, full system trace: counters moving through each component | **Receipts come back.** | **T5** "Messages go out through each channel. Delivery and replies come back as webhooks, and duplicates are ignored." |
| 6 | 0:54–1:06 | **Intent ladder**. Lineage caption: "≈ reject option · Chow 1970" | CM-03 engine trace (inbound page, Technical): rules → small model → hand-off; OTP state | **Unsure? A person decides.** | **T6** "Replies climb a ladder: rules first, then a small model. Thresholds are hand-set, and when it's unsure, it hands off to a person." |
| 7 | 1:06–1:18 | **People** (attached to Aladdin) | Inbound agent console: **Take over** → audit-log row; outbound **Approve** a held call | **Every action audited.** | **T7** "People can step in at any moment. Every action still passes Aladdin, and lands in the audit log." |
| 8 | 1:18–1:30 | **Outcomes → K09** (the loop closes); one pulse runs end to end | Terminal: `run_k09_replay.py --no-explore --no-context --no-drift --check-determinism` → **IDENTICAL** hash (real run, 638 s, sped up and labelled) | **Same seed. Same hash.** | **T8** "Every outcome feeds the learner. Same data in, same decisions out." |
| 9 | 1:30–1:40 | — | Terminal, sped up: `run_redteam.py` → **49 PASS, 0 FAIL**; `bench_ledger.py 50000` → **50,000 decisions in 11.1 s, 0 cap breaches**; `pytest` → **3,030 passed** (real runs, 20 Nov) | **On our own red-team suite.** | **T9** "We attack it, load it, and test it, on every push." |
| 10 | 1:40–1:45 | — | **Built on research** end card (static, 32 px): Thompson, *Biometrika* 1933 · Chapelle & Li, NeurIPS 2011 · Li et al., WWW 2010 · Badanidiyuru, Kleinberg & Slivkins, FOCS 2013 · Clayton & Kaldor, *Biometrics* 1987 · Abe et al., KDD 2010 · Yancey & Settles, KDD 2020 · Alshiekh et al., AAAI 2018 | **Built on research.** | — |
| 11 | 1:45–1:48 | Full diagram at 1.0×, one last pulse end to end | — | — | — |
| 12 | 1:48–1:53 | **Hard stop to lockup** | Logo; credits (music The_Mountain / Pixabay; voice Sarvam Neha; SIMULATED) | **Built to be trusted with every rupee.** | — (silence) |

**Narration:** 9 lines, 133 words, about 50 s, so voice is on for about 44% of the runtime.

## Production method

1. **Capture.** `tools/record.swift` drives each real page in an offscreen WKWebView at 2× pixels (snapshot memory is freed per frame). JSON action scripts click real buttons and type real text. It writes keyframes, bursts and a log of every click's target rectangle.
2. **Compose.** `comp/engine.js` is a deterministic `seek(t)` that implements the 12 moves as data. Film files: `films/product.js` and `films/technical.js`.
3. **Render.** `tools/render.swift` takes a WKWebView snapshot per frame and writes H.264 with AVAssetWriter. Then the AAC mix is muxed in, and a 1280×720 copy is exported.
4. **Sound.** `tools/mix.swift` does the bar-line music edit, places the narration, ducks the music 18 dB under the voice, measures BS.1770 loudness, normalises to −14 LUFS and applies a −1 dBTP ceiling.

## Claim map

| Claim | Source | Label |
|---|---|---|
| Fixed schedule: 1,262 messages → 23 replies | `/demo/live/state` after the seed-42 run (`counters.cn10.sent` = 1262, `replies` = 23); coordinator note | SIMULATED, seed 42 |
| ≈2× money back per ₹ vs a fixed schedule | `backend/README.md` final unseen seeds 517/272, 530/229, 459/268; `film/TRUTH_TABLE.md` | SIMULATED · 3 unseen seeds · fixed-schedule stand-in |
| 0 rule breaches | PROGRESS §1 "0 K01 breaches and 0 K15 violations"; `/demo/live` "Rule breaches 0" | SIMULATED |
| 49/49 attacks | real `run_redteam.py` output (20 Nov, kairos-main 0778ce8) | on our own red-team suite |
| 50,000 decisions in 11.1 s, 0 cap breaches | real `bench_ledger.py 50000` output (20 Nov) | real run |
| 3,030 tests passed | real `pytest` run (20 Nov, kairos-main) | real run |
| Same seed, same hash | real `--check-determinism` run: "Deterministic replay: IDENTICAL" | real run |
| Checked twice (reserve + send-time re-check) | PROGRESS §2 K06; papers.md (gate + atomic reserve + dispatch re-check) | — |
| Bayesian learner, per segment and per borrower | papers.md: Beta-Bernoulli posterior mean; pooled fallback | never "Thompson" / "RL" |
| Hand-set thresholds, hand-off to a person | papers.md §D6 (0.85 / 0.70) | never "calibrated" |
| No details before OTP; doctored screenshot caught; STOP every channel | PROGRESS §1 CM-03 and CM-02; shown live on the inbound page | SIMULATED |
| Built-on citations | papers.md §B (all 8 VERIFIED); lineage captions Alshiekh 2018, Chow 1970 | "Built on / draws on", never "implements" |
| "Pause outbound" (if shown) | global or per-borrower pause only | never "pause campaign" in narration |

SMS and WhatsApp sends in the demo are dry-run; nothing in either film is sent to a real person.
