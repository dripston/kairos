# Kairos film

A 2:19.6 (139.635 s) explainer film about Kairos for the CreditNirvana Agentathon (an unofficial team film by
The A Team). It is made for people who don't work in collections. One voice (Neha) explains it, and every line
gets the picture that proves it. Everything shown is synthetic and SIMULATED.

| File | What |
|---|---|
| `kairos_film.html` | The film: 17 scenes, captions, every number (in `CONFIG`), the hits list and the audio player |
| `audio/vo_neha/` | Neha's 17 lines (`01_people.wav` … `17_end.wav`), `lines.json` (text) and `timeline.json` (start/end of every line; **all timing comes from here**) |
| `music/vo_hero.m4a` | Neha's lines placed on the timeline, cleaned and levelled (made by `music/mix.py`) |
| `music/original/` | The original score from the composer (`v3_cinematic_trailer.m4a` and `v3_hits.json`) |
| `music/mix.py` | Renders `vo_hero.m4a`; with `--music` also a ducked soundtrack for exporting an MP4 |
| `assets/real/` | Real Kairos screens captured from our repo (synthetic data); see `TRUTH_TABLE.md` |
| `assets/broll/` | Pexels footage (`SOURCES.md`, `fetch_broll.sh`); the MP4s are not committed |
| `tools/serve.py` | Local server with seeking support (needed for the audio) |
| `tools/posters.swift` | Poster frames for the b-roll, so a clip never flashes black while it loads |
| `tools/mux.swift` | Puts a soundtrack under a screen recording and exports a 1080p MP4 |
| `tools/shotlist.py`, `tools/make_script.py` | Regenerate `shot-list.csv` and `SCRIPT.md` from the film and the timeline |
| `tools/sarvam_vo_neha.py` | Re-records Neha's lines with Sarvam bulbul:v3 (reads the key from a `.env` path, never prints it) |
| `CREATIVE_BRIEF.md`, `TRUTH_TABLE.md`, `QA.md`, `SCRIPT.md`, `SHOT_LIST.md` | Brief, claim sources, QA, timed script, shot plan |

## Play it

```bash
bash film/assets/broll/fetch_broll.sh       # once: download the Pexels clips (git-ignored)
swift film/tools/posters.swift film/assets/broll city_aerial_mumbai_night=4 phone_notification_closeup=1 \
  woman_phone_bed=2 snoozing_phone=3 kolkata_buses=6 hands_laptop_dark=3 man_turban_phone_night=1 man_good_news_phone=3
python3 film/tools/serve.py 8766            # then open http://127.0.0.1:8766/kairos_film.html
```

A plain `python3 -m http.server` can't seek audio, so use `serve.py`. Without the clips the film still plays;
those shots show their poster frame, or black if the posters weren't made either.

The film opens in presentation mode, with no player chrome. Move the mouse to show a thin control strip,
which hides again after 2 s. `?controls=1` keeps it visible.

| Control | Key |
|---|---|
| Play / pause | Space |
| Previous / next scene | ← / → |
| Captions on/off (on by default) | C |
| Music on/off | M |
| Voice on/off | V |
| Record mode: hides everything, restarts at 0:00 after 1.5 s | R (Esc exits) |

URL options: `?t=38`, `?scene=n09`, `?autoplay=1`, `?record=1`, `?cc=0`, `?controls=1`.

## Sound

- **Voice:** Neha only (Sarvam bulbul:v3, pace 1.2, AI-generated). The voice track is the master clock.
- **Music:** one config line near the bottom of `kairos_film.html`:

  ```js
  const MUSIC_SRC = 'music/original/v3_cinematic_trailer.m4a';   // missing file = silent
  ```

  If the file is missing, the film plays with the voice alone. The player ducks the music 11 dB under every
  line (20 ms attack, 300 ms release) and fades it out over the last 2.5 s.
- **Hits:** `music/original/v3_hits.json` drives the picture events. Any cut within 0.45 s of a hit (0.85 s for a
  big one) snaps onto it as a hard cut, and every hit gets a 2-frame flash and a 1.00 → 1.04 punch-in over 120 ms.
  The composer's types map to `big` (big, biggest), `boom` (cold_open, boom) and quiet (riser, cut, pullback: no
  cut, no flash). Without the file, a built-in copy of the same hits is used. The two big hits are 26.28 s
  ("Meet Kairos", the laptop lights up) and 118.09 s (the ~2×). The STOP frame holds still through the silence
  at 72.9–73.7 s.

## Export an MP4 with sound

```bash
python3 film/music/mix.py --music film/music/original/v3_cinematic_trailer.m4a   # writes music/soundtrack_hero.m4a
```

1. Open the film in Chrome, full screen on a 1080p display, and start a screen recording (Cmd+Shift+5, no microphone).
2. Press **R** and let it play to the end. Stop the recording.
3. `swift film/tools/mux.swift ~/Desktop/Recording.mov film/music/soundtrack_hero.m4a kairos.mp4 1.5`. The last
   number trims the seconds before the film's 0:00 (record mode holds 1.5 s; add the time before you pressed R).

## Change the words or the timing

1. Edit `audio/vo_neha/lines.json`, re-record with `tools/sarvam_vo_neha.py`, and rebuild `timeline.json`. The rule
   is in its `rule` field: 1.5 s lead, 0.6 s gaps, +1.2 s before 04 and 15, +0.3 s before 11, 3 s end hold.
2. Update `ALL_SCENES`, `VO_DUR` and `VO_TEXT` in `kairos_film.html` to the new timeline.
3. Regenerate everything that depends on it:

   ```bash
   python3 film/music/mix.py && python3 film/tools/shotlist.py && python3 film/tools/make_script.py
   python3 -I <skill>/scripts/validate_shotlist.py film/shot-list.csv --target <runtime_s>
   ```

Every number on screen lives in `CONFIG` at the top of `kairos_film.html`, and each one is sourced in `TRUTH_TABLE.md`.

## Credits

Voice: Sarvam bulbul:v3 (Neha), AI-generated · Stock footage: Pexels (pexels.com/license) · Music: original score
(`music/original/`) · Real product screens: Kairos, synthetic data · Film: The A Team.
