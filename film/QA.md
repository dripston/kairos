# Kairos film: QA (skill pack `references/verification-qa.md`)

Checked 19 Nov 2026 on the full film (216 s) and the operator cut (89 s). The checks were done in the
browser pane at 1920×1080 and 1280×720: every scene screenshotted, both morph transitions checked
mid-flight, and soundtrack-to-picture drift logged.

## Creative QA (0–5)

| # | Criterion | Full | Operator | Notes |
|---|---|---|---|---|
| 1 | Hook | 4 | 4 | A phone buzzes at 0:01 with the same reminder; by 0:03 the problem is clear. Operator: "A day at the desk" + Priya by 0:04 |
| 2 | Product identity | 4 | 5 | KAIROS mark on screen from frame 1; full cut names it in VO at 0:19.8 and shows it deciding at 0:24 (later than the skill's 10 s ideal; the hook needs the "before" world). Operator names Kairos at 0:00.2 |
| 3 | Explaining features | 5 | 5 | Three benefits each shown as use → close-up → result (decide per person; safe; pays and runs itself), with two real CRM / CM-03 screens |
| 4 | Emotional promise | 4 | 5 | Relief for Ravi (no nagging, a plan) and for Priya (goes home on time) |
| 5 | Distinctiveness | 4 | 5 | Cricket scoreboard (rupees as runs, rule breaches as wickets); phone screen morphs into the real CRM |
| 6 | Craft | 4 | 4 | Eased cross-dissolves with blur/scale, camera drift, parallax, morphs; original score that changes on every cut; synthesised foley; music ducked about 10 dB under every line; a quiet beat before the final line. The macOS draft voices sound synthetic (replace for release) |
| 7 | Physical accuracy | 4 | 4 | One phone frame and one laptop frame reused throughout; real screens unaltered except crop/zoom; UI states follow the story (pause → verified → only Loan A cancelled) |
| 8 | Truthfulness | 4 | 4 | Every on-screen number is in `TRUTH_TABLE.md` with source and qualifier; "SIMULATED data" on the proof scene and on the scoreboard; 148/148 cites `POLICY_COVERAGE.md` with the K10 identity-mode caveat; ~2× footnote says Kairos spent less of the same budget |
| 9 | Accessibility | 4 | 4 | Captions on by default, speaker names on character lines, Hindi lines with English translations. Contrast: captions 18.5:1, body 6–8:1, footnotes raised to ≥ 4.6:1 (was 3.1:1). Footnotes are 18–19 px at 1080p: small, legible on a laptop and not on a phone |
| 10 | Practicality | 5 | 5 | Everything renders locally from code; recording + `mux.swift` gives a 1080p MP4 with sound |
| | **Total** | **42 / 50** | **45 / 50** | **Release gate (≥ 42 and #3, #7, #8 ≥ 4): PASS / PASS** |

## Final checks

| Check | Result |
|---|---|
| Timeline sums to runtime, no gaps/overlaps | PASS: `validate_shotlist.py` full 36 shots / 216 s, operator 12 shots / 89 s; cross-dissolves are 0.9 s centred on each cut |
| End title and CTA coherent; no unauthorised brand likeness | PASS: Kairos lockup + team/event line; CreditNirvana and Sarvam as plain text only; no logos |
| Rights: music, voices, brand assets | Music and foley original (`music/LICENSE.md`). Voices are macOS `say` drafts: check Apple's terms or replace before public release |
| No copied ad structure, copy or soundtrack | PASS: own structure (borrower → operator → proof), own copy, generated score |
| No WhatsApp / Apple trade dress | PASS: generic chat UI (slate/teal bubbles, "Lender · verified", no green, no logo); device frames have no logos; "WhatsApp" only in text and VO |
| 16:9 + 9:16 reframing | PARTIAL: 16:9 master done; 9:16 is a plan in `SHOT_LIST.md`, not built |
| Spoken VO fits with pauses and music | PASS: `compose.py` measures every line; 0 overlaps in both cuts (5 lines under 0.05 s past the 0.12 s safety margin); about 139 wpm (full) / 128 wpm (operator) |
| Feature-to-proof map matches screen action | PASS (`CREATIVE_BRIEF.md`, `shot-list*.csv` feature_proof column) |
| Factual caveats present and legible | PASS: proof footnote, "illustrative" tags on chart, scoreboard and segment table; caption tracks the VO |
| Viewer can recall one benefit | "Right message, right time, or none at all", plus "about twice the money per rupee" said twice (o5, s8) |
| Central feature never shown? | None missing: decide (s3–s4), safety (s5, o3), human control (s6, o3), learning (s7), proof (s8) |
| Audio/picture sync | Drift 20–30 ms measured (film 193.40 / audio 193.38 …). Needs a server with Range support (`tools/serve.py`) or file:// to seek |
| Reduced motion | Camera drift, blur, morphs, Ken Burns, coin fall and cursor travel are all off under `prefers-reduced-motion`; fades only |

## Repairs made during QA
- Re-timed 20+ cues so every voice line fits at natural pace (s4 grew 27 → 29 s; s5, s7, o2, o4, s8 shifted).
- "148/148 policy cases" was first removed (no source found), then restored with its source, `POLICY_COVERAGE.md` (K01 38, K10 33 in k10 identity mode, K13 25, K21 20, K24 32).
- Compact control bar under 1100 px wide (play/pause, time, scrubber, "⋯" menu for scenes and toggles); hides after 2 s while playing.
- Added the budget qualifier to the ~2× claim; raised footnote contrast to AA.
- Fixed: stale caption after seeking; washed-out real-screen backdrops; stamp text wrapping; audio clock
  adoption when the server can't seek.

## Known risks
- Draft TTS voices are clearly synthetic; the Hindi read-back by Lekha is good, the English narrator a bit flat.
- The scoreboard is a rebuild (no `/demo/live` on main yet), so it is labelled illustrative; swap in the real screen when it lands.
- The React demo UI (Control Room, Learning) was not captured: no Node on this Mac.
