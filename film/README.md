# Kairos film

An explainer film for the CreditNirvana Agentathon (an unofficial team film by The A Team). It is made
for people who don't work in collections. Everything shown is synthetic and SIMULATED.

| Cut | Length | Open |
|---|---|---|
| Full film: borrower's story → operator's day → proof → close | 3:36 (216 s) | `kairos_film.html` |
| Operator cut: a day at the desk, cricket scoreboard | 1:29 (89 s) | `kairos_film.html?cut=operator` |

| File | What |
|---|---|
| `kairos_film.html` | The film. The scene table, captions and every number (in `CONFIG`) live here |
| `assets/real/*.jpg` | Real Kairos screens captured from `origin/main` (synthetic data); see `TRUTH_TABLE.md` |
| `music/compose.py` | Generates the original music, draft character voices, foley and the mixed soundtrack |
| `music/*.m4a` | Soundtracks the film plays (music, voices, both) for each cut |
| `music/cue_sheet_*.json` | Timing log: sections, bars, chords, every voice line (start, length, gap), loudness |
| `tools/mux.swift` | Puts the soundtrack under a screen recording and exports a 1080p MP4 (AVFoundation, no ffmpeg) |
| `tools/serve.py` | Local server with seeking support |
| `tools/shotlist.py`, `tools/make_script.py` | Regenerate `shot-list*.csv` and `SCRIPT.md` from the scene table |
| `CREATIVE_BRIEF.md`, `TRUTH_TABLE.md`, `QA.md` | Creative thesis, claim sources and qualifiers, QA results |
| `SCRIPT.md`, `shot-list.csv`, `shot-list-operator.csv` | Timed script and frame-accurate shot lists (30 fps) |
| `SHOT_LIST.md` | Live-action / screen-capture plan and the 9:16 cut |

## Play it

```bash
python3 film/tools/serve.py        # then open http://127.0.0.1:8765/kairos_film.html
```

Double-clicking the HTML also works. A plain `python3 -m http.server` will play the film, but the browser
can't seek the soundtrack with it, so use `serve.py`.

| Control | Key |
|---|---|
| Play / pause | Space |
| Previous / next scene | ← / → (or click a scene; the top row jumps between Borrower's story, Operator's day, Proof & close) |
| Captions on/off (speaker names shown for character lines) | C |
| Music on/off | M |
| Voices on/off (narrator + characters) | V |
| Browser voice (built-in speech, a fallback; off by default) | N |
| Record mode: hides controls and cursor, turns music and voices on, restarts at 0:00 after 1.5 s | R (Esc exits) |

In windows narrower than 1100 px (e.g. the app's browser pane) the controls shrink to a bottom bar: play/pause,
time, scrubber and a "⋯" menu with the scene list and toggles. They hide after 2 s of playback and come back on mouse move.

URL options: `?cut=operator`, `?scene=4` or `?scene=o2`, `?group=operator`, `?t=38`, `?autoplay=1`,
`?record=1`, `?cc=0`. The soundtrack is the master clock while it plays (drift under 30 ms in testing).
The film follows the system "Reduce motion" setting: no camera drift, blur, morphs or flying elements, only
fades.

## Music and voices

```bash
python3 film/music/compose.py                 # both cuts (about 4 minutes)
python3 film/music/compose.py --cut operator  # one cut
python3 film/music/compose.py --no-voice      # music only (no macOS `say` needed)
```

The script reads the scene table straight from `kairos_film.html`, so the music changes section on every
scene cut and every line starts on its caption. It writes `kairos_theme_<cut>.wav` (music), `vo_<cut>.wav`
(voices) and `soundtrack_<cut>.wav` (music ducked about 10 dB under every line, plus foley and voices),
44.1 kHz stereo, peak −1 dBFS, about −16 dBFS RMS. It also writes an `.m4a` copy of each for the film. The WAVs
are git-ignored and can be regenerated.

- **Music, "Kairos Theme":** original, built from code with Python's standard library only and a fixed seed. It has soft
  detuned-saw pads with a filter-style swell, a gentle pluck arpeggio, sub-bass, and a synthesised kick, clap and hats.
  There's multi-tap reverb, crowd swells made from filtered noise in the scoreboard, a soft whoosh across
  every cut and a quiet beat before the final line. Sections per scene: the problem is tense minor
  (Am–Fmaj7–Dm–E); Kairos lifts to major (C–G–Am–F); the journey has a steady pulse; safety is calm;
  learning builds; proof is uplifting; the scoreboard has "match" energy with the crowd; the close resolves
  F–G–C. It runs at 96 BPM (one bar = 2.5 s). See `music/cue_sheet_*.json` for every bar.
- **Foley:** UI clicks, ticks, pops, chimes, phone buzz, a stamp thunk and a laptop close, all synthesised and placed on
  the visual events.
- **Character voices, "draft TTS (macOS voices)":**

  | Speaker | Voice | Example line |
  |---|---|---|
  | Narrator | Rishi (en_IN) | "Aladdin, our supervisor, checks every message. Twice." |
  | Ravi, the borrower | Aman (en_IN), softer with more room for his inner voice | "Need time. Salary late hai." / "Haan, sattaees tareekh tak kar doonga." |
  | Ritu, the voice bot | Lekha (hi_IN) | "रवि जी, क्या आप 27 तारीख तक भुगतान कर पाएँगे?" … "ठीक है, तो 27 तारीख पक्की।" |
  | Priya, the operator | Tara (en_IN) | "Zero wickets, again." / "Approve." |

  The character lines get a small room reverb; the narrator stays dry and close. A final version could use
  Sarvam TTS (the team's Ritu and Advait voices) or human readers. Replace the files in `music/` and keep the
  names. Nothing here calls an API.

## Record it to MP4 with sound

1. Start `tools/serve.py`, open the film in Chrome, and make the window full screen on a 1080p display (or
   any 16:9 window; the film scales).
2. Press Cmd+Shift+5 → **Record Entire Screen** (or **Selected Portion** around the film), with no microphone.
3. Click into the film, press **R**, and let it play to the end. Stop the recording.
4. Add the soundtrack. A screen recording usually has no system audio, so mux the rendered soundtrack:

   ```bash
   swift film/tools/mux.swift ~/Desktop/Recording.mov film/music/soundtrack_full.wav kairos_full.mp4 1.5
   ```

   The last number trims the recording's first seconds so the film's 0:00 lines up with the soundtrack.
   Record mode holds the first frame for 1.5 s; add the time between starting the recording and pressing R.
   The output is a 1080p H.264 + AAC MP4. For the operator cut, use `soundtrack_operator.wav`.

   To test the tool: `swift film/tools/mux.swift --make-test-clip /tmp/test.mov 3` makes a 3 s dummy clip.
   Without Swift: drag the .mov into iMovie, detach or mute its audio, drop `soundtrack_*.wav` at 0:00, align
   it to the first frame and export at 1080p.

## Change the numbers or the words

- Every number lives in `CONFIG` at the top of `kairos_film.html`. The spoken versions of the proof numbers are in
  `CONFIG.spoken`.
- Lines and timings live in the scene table (`ALL_SCENES`) just below. Each cue is
  `[seconds, caption, speaker?, text for TTS?]`.
- After any wording or timing change, regenerate everything that depends on it:

  ```bash
  python3 film/music/compose.py && python3 film/tools/make_script.py && python3 film/tools/shotlist.py
  python3 -I <skill>/scripts/validate_shotlist.py film/shot-list.csv --target 216
  ```

  `compose.py` logs any line that doesn't fit before the next one.
