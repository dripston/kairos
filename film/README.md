# Kairos film (first cut)

A 2:00 explainer for the CreditNirvana Agentathon, for people who don't work in collections. It covers what
Kairos is, why it matters and how it works. Everything shown is synthetic and SIMULATED.

| File | What |
|---|---|
| `kairos_film.html` | The film: one self-contained HTML file (no internet, no external files), 16:9, 9 scenes, 2:00 |
| `SCRIPT.md` | Scene-by-scene script: timecodes, voice-over, on-screen text, visuals, product screen to record |
| `SHOT_LIST.md` | Live-action / screen-capture version with the exact commands, plus a 9:16 social cut plan |

## Play it

Double-click `kairos_film.html` (Chrome or Safari). If the browser blocks local files, serve the folder
instead: `python3 -m http.server 8765` in this folder, then open http://127.0.0.1:8765/kairos_film.html.

| Control | Key |
|---|---|
| Play / pause | Space |
| Previous / next scene | ← / → (or click a scene in the bar) |
| Scrub | drag the slider |
| Captions on/off | C |
| Narration on/off (browser voice, off by default) | N |
| Record mode (hides all controls and the cursor, restarts at 0:00 and plays after 1.5 s) | R; Esc exits |

URL options: `?scene=4` (jump to a scene), `?t=38` (jump to a second), `?autoplay=1`, `?record=1`, `?cc=0`
(captions off).

The film draws on a fixed 1920×1080 canvas and scales to fit the window, so it looks the same at any size.
It follows the system "Reduce motion" setting (fades only, no shaking, sliding or falling coins).

## Record it to MP4 (1080p)

1. Make the window exactly 1920×1080 and full screen: in Chrome, View → Enter Full Screen (Ctrl+Cmd+F) on a
   1080p display, or on a Retina Mac use a 1920×1080 external display / "Larger Text" scaled mode so the window
   is 1920×1080 points.
2. Press Cmd+Shift+5 → **Record Entire Screen** (or **Record Selected Portion** around the window). In
   Options, turn off "Show Mouse Clicks" and pick no microphone.
3. Click **Record**, click into the film and press **R**. Controls disappear, the film holds its opening frame for 1.5 s,
   then plays 2:00 and stops on the last frame.
4. Stop recording from the menu bar. Trim the head and tail in QuickTime (Edit → Trim), then File → Export As →
   **1080p**.

Captions are part of the picture. For a clean plate (captions added later in the editor), press **C** before
**R**.

## Change the numbers

Every number, the team name ("The A Team"), the product name and the event name live in the `CONFIG` block
at the very top of `kairos_film.html`:

```js
const CONFIG = {
  product: "Kairos", team: "The A Team", event: "CreditNirvana Agentathon 2026", borrower: "Ravi",
  numbers: { liftPct: 50, liftSeeds: "+48% / +61% / +56%", ruleBreaches: 0, decisions: 50000, ... },
  spoken: { lift: "around fifty percent more replies per rupee, and zero rule breaches.", ... }
};
```

Edit, save, reload. The on-screen numbers and count-ups read from `numbers`. The narrator's wording for the
proof scene is in `spoken`, so update it when a headline number changes (and update `SCRIPT.md` to match).
Scene lengths and caption timings are in the `SCENES` list just below `CONFIG`.

## Add a voice-over later

The captions are the voice-over script, timed to the picture (`SCRIPT.md` has the same lines with timecodes).
Two ways:

- **Record a human voice**: read `SCRIPT.md` against the playing film (captions on as a teleprompter), then lay
  the audio under the MP4 in iMovie / CapCut / DaVinci Resolve and nudge each line to its scene's start.
- **Synthetic narration**: Sarvam's text-to-speech with the same Ritu (female) or Advait (male) voices the
  Kairos voice bots use would keep the film consistent with the product. Generate one clip per scene from
  `SCRIPT.md` and place each at the scene's timecode. (Not wired up here; nothing in this folder calls an API.)

The **N** toggle uses the browser's built-in speech (an en-IN voice if the computer has one) and is only a
preview of pacing. Keep it off when recording.

## What is placeholder in this cut

- All borrower details, the messages on the phones, the Hindi call, the "1,240 borrowers" approval and the
  audit times are illustrative, made for the film.
- The learning chart (WhatsApp dropping at day 7, the "where Kairos sends" split) and the region chips are
  illustrative of what the K09 replay shows. They are not plotted from a real run.
- Headline numbers come from the current SIMULATED results (K09 vs CN10, PR #17; red-team 49/49; CM-03 eval).
  Re-check them in `CONFIG` before the final export. The newer opt-out-aware setting and the payment-aware
  version are not in the film yet.
- No voice track yet (captions only).
