# Kairos film: QA (skill pack `references/verification-qa.md`)

Checked 20 Nov 2026 on the final film (139.635 s, 17 scenes): headless Chrome at 1280×720 (17 key beats) and
1920×1080 (spot checks), and the browser pane at 1280×720 (playback, seeking, console). No JS errors. The only
console error is the 404 for `music/original/v3_cinematic_trailer.m4a`, which isn't delivered yet; the silent
fallback works and the film plays on Neha alone.

## Creative QA (0–5)

| # | Criterion | Score | Notes |
|---|---|---|---|
| 1 | Hook | 4 | Real Indian street at 0:00 with the first line at 1.5 s; the problem (same message, same time) is on screen by 0:09 |
| 2 | Product identity | 4 | Named and shown at 0:26.3 on the big hit (laptop lights up). Later than the skill's 10 s ideal, because the "before" world needs 25 s for laypeople |
| 3 | Explaining features | 5 | One line, one proof: CRM facts → contact or wait → channel and time → two checklists → BLOCKED / STOP → chat in Hindi → OTP → fake caught → learning → approval |
| 4 | Emotional promise | 4 | Calm relief: fewer, better messages; ends on a man smiling on a call |
| 5 | Distinctiveness | 4 | Apple-launch staging of a collections product; still STOP frame in the score's silence; giant ~2× on the biggest hit |
| 6 | Craft | 4 | One ease for all camera moves, motion blur, masked type, hard cuts on hits with 2-frame flash and 4% punch, dissolves elsewhere, grain, graded b-roll. Score not yet in, so the hit design is unheard |
| 7 | Physical accuracy | 4 | One laptop and one phone model reused; real screens unaltered except crop and zoom; chat states follow the voice (typing dots → reply → pay link) |
| 8 | Truthfulness | 5 | Every number in `TRUTH_TABLE.md` with source and qualifier; "In our tests" + SIMULATED badge + seed fine print on ~2×; 148/148 cites `POLICY_COVERAGE.md` with the K10 identity-mode caveat; cost counter and learning bars labelled illustrative |
| 9 | Accessibility | 4 | Captions on by default, exact VO, two lines max; the Hindi line is captioned in English and repeated on screen. Caption text is white on a soft dark shadow and feathered backdrop (added in QA; was weak over the light studio scenes) |
| 10 | Practicality | 5 | Renders locally; screen recording + `mux.swift` gives a 1080p MP4 with sound |
| | **Total** | **43 / 50** | **Release gate (≥ 42 and #3, #7, #8 ≥ 4): PASS** |

## Final checks

| Check | Result |
|---|---|
| Timeline sums to runtime, no gaps/overlaps | PASS: `validate_shotlist.py film/shot-list.csv --target 139.635` → "PASS: 17 shots, 139.635s, contiguous timeline" |
| Timing from `timeline.json` | PASS: VO at 1.5 s, 0.6 s gaps, +1.2 s before 04/15, +0.3 s before 11, 3 s end hold; markers lift 26.08, climax 117.89, final line 131.31. Player scene starts match `shot-list.csv` to the millisecond |
| Hits | PASS: 13 composer hits loaded from `v3_hits.json`; cuts snapped onto 43.28, 58.87, 66.65, 76.87, 91.34, 101.23, 118.09, 126.7; the pull-back at 84.8 stays a dissolve; riser and groove cut give no flash |
| Voice | Neha only (Sarvam bulbul:v3, pace 1.2), **AI-generated**, credited on the end card. No male voice; `vo_sarvam/` deleted |
| Music | `MUSIC_SRC` one line; missing file = silent (verified). Ducking −11 dB under Neha, 20 ms / 300 ms. Pixabay track and `compose.py` not used |
| Rights | Pexels footage under the Pexels License (`assets/broll/SOURCES.md`), not committed; credit "Stock footage: Pexels" on the end card. Real screens from our repo with synthetic data |
| Brand likeness | Device renders have no logos. The chat has a WhatsApp-like look (teal header, beige pattern), as asked, but no WhatsApp logo or name in the UI; header reads "Lender ✓ · verified business". "WhatsApp" appears only in VO and type, as a fact |
| Device screens | PASS: every laptop and phone carries a screen (real crop, rebuilt view or chat); chat phones are 74–77% of frame height |
| Factual caveats present and legible | PASS: SIMULATED badges on n05, n13, n14, n15, n16; fine print on ~2× (12–15 px at 720p: readable on a laptop, not a phone) |
| Viewer can recall one benefit | "The right message, at the right time. Or none at all." and "about twice as much money for every rupee" |
| Secrets | No `sk_` keys in the tree (scan before commit) |

## Repairs made during QA
- Composer hit types (`biggest`, `medium`, `pullback`, `riser`, `cut` …) mapped to picture events, so 118.09 is a BIG hard cut and the pull-back, riser and groove cut don't flash.
- Caption backdrop added for the light studio scenes.
- Pixabay music credit replaced by "Music: original score" on the end card.
- n12 had an empty right half: added "Identity first." Tightened the n09 STOP push-in; fixed descender clipping in masked type.

## Known risks
- The score (`v3_cinematic_trailer.m4a`) isn't delivered yet, so hits, ducking and the 72.9 s silence were checked on picture only.
- n13's learning bars and n14's dashboard are rebuilt product views (n14 uses real `/demo/live` crops); both are labelled.
- n16 holds 3.5 s: "0" for 1.8 s, then 49/49 and 148/148 from about 128.7 s (≈ 1.4 s before the cut to the end card). Brought in 0.5 s earlier during QA.
