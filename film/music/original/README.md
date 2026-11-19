# Kairos film: original score

Original composition; instruments: Apple General MIDI DLS (bundled with macOS).
The trailer impacts (booms, sub drops, thumps, risers, reversed crashes, clock ticks, braam layer) are
synthesised in code by `tools/score_render.swift`; no third-party samples, loops or recordings are used,
and no melody or arrangement is taken from an existing song.

Every file is exactly as long as its voice timeline, as 44.1 kHz stereo 16-bit WAV plus AAC 256 kb/s M4A:
V3 (full film) 247.665 s from `audio/film_timeline.json`; V3 short cut 119.287 s from
`audio/film_timeline_short.json`; V3 technical film 135.64 s from `audio/film_timeline_tech.json`; V1 and V2 139.635 s from the superseded `audio/vo_neha/timeline.json`.

## How it is made

1. `tools/score_compose.py <variant> score.json` writes the composition as note events
   (time, track, MIDI note, velocity, duration), controller events, volume automation and
   synthesised-fx events. A piecewise tempo map puts every scripted hit on a downbeat.
   Humanisation: timing ±6 ms (drums ±3–4 ms), velocity ±8; hits are placed exactly.
2. `tools/score_render.swift` renders it with `AVAudioEngine` in offline manual-rendering mode.
   Each track is an `AVAudioUnitSampler` loading
   `/System/Library/Components/CoreAudio.component/Contents/Resources/gs_instruments.dls`
   (melodic bank 0x79, percussion bank 0x78), then an `AVAudioUnitEQ` (high-pass, a 2.8 kHz cut so
   the narrator sits on top, low-pass), optional `AVAudioUnitDistortion` (braam saturation) and
   `AVAudioUnitDelay`, into a track mixer with a send to a shared `AVAudioUnitReverb` hall.
   Notes are fired between 64-frame (1.45 ms) render slices. Synthesised fx are played through
   `AVAudioPlayerNode` tracks in the same graph, so they share the hall reverb.
3. Mastering (same tool): master EQ (32 Hz high-pass, −2.5 dB at 2.6 kHz, +1.5 dB air shelf),
   2:1 RMS glue compressor, normalise to −16 LUFS integrated (BS.1770 gated), 4 ms look-ahead
   limiter at −1 dBFS, TPDF dither to 16-bit. M4A via `afconvert -f m4af -d aac -b 256000`.
4. `tools/score_analyze.swift` prints the verification numbers below.

Rebuild (V3 shown; V1 and V2 are the same with `v1` / `v1_uplifting` and `v2` / `v2_modern_tech`):

```
swiftc -O film/tools/score_render.swift -o /tmp/score_render
python3 film/tools/score_compose.py v3 /tmp/v3.json film/music/original/v3_hits.json
#   short cut: v3_short /tmp/v3s.json film/music/original/v3_hits_short.json -> v3_trailer_short.wav
#   technical film: v3_tech /tmp/v3t.json film/music/original/v3_hits_tech.json -> v3_tech.wav
/tmp/score_render /tmp/v3.json film/music/original/v3_cinematic_trailer.wav
afconvert -f m4af -d aac -b 256000 film/music/original/v3_cinematic_trailer.wav film/music/original/v3_cinematic_trailer.m4a
python3 film/tools/score_page.py     # refreshes the rows in audio/check.html
```

## V3 "Cinematic Trailer" (full film): `v3_cinematic_trailer.wav` / `.m4a`

Scored to `audio/film_timeline.json` (247.665 s, 22 narrator + 15 dialogue lines).

- **Tempo:** about 99 BPM (beats of 0.56–0.65 s; a tempo map puts every hit on a downbeat).
- **Key:** D minor; C minor for "Under the hood" (159.72–218.76); back towards D for the operator lift;
  D minor for the proof, ending in D major (B♭ – C – D).
- **Instruments (GM):** String Ensemble 48 (16th-note ostinato, galloping in the channels scene, big chords),
  Slow Strings 49, Tremolo Strings 44 (fake-proof suspense), Pizzicato 45, Timpani 47, Taiko 116, GM kit,
  Trombone 57 + Tuba 58 + Brass Section 61 through saturation (braams), French Horns 60, Choir Aahs 52 (climax
  and ending only), Piano 0 (the motif A–D–F–E, major at the end), Warm Pad 89, Celesta 8 (channel flourish and
  phone ring), Reverse Cymbal 119; for "Under the hood": Synth Bass 2 39 (16th arpeggio), Electric Piano 2 5
  (off-beat arp), Synth Bass 1 38 (sub pulse), Polysynth 90 and Halo 94 pads; plus synthesised booms, sub drops,
  thumps, risers, reversed crashes, clock ticks and a braam layer.
- **Dialogue scenes are pad only** (warm pad and low strings, tremolo in the fake-proof scene), no drums and no
  melody, sitting about 10 dB under the narrator bed.
- **Hit map** (also in `v3_hits.json`, with the tempo anchors and the dialogue ranges):

| Time (s) | Hit / texture | Cue |
|---|---|---|
| 0.00 | cold-open boom + sub drop, pizzicato and clock-tick pulse | problem |
| 24.60–25.98 | riser, then near-silence | |
| 26.28 | BIG hit; strings ostinato + taiko groove | 04_meet "Meet Kairos" |
| 43.28 | medium | 06_decides |
| 48.29 | channel-ring flourish (celesta + pizzicato run), galloping ostinato, ticking hats | 07_channel |
| 56.51 | medium + brass stab, low trombone pad | 08_aladdin |
| 64.29 | light, sparse groove | 09_rules |
| 71.35 | stop-hit, then near-silence to 74.5 | "everything stops" |
| 74.5–79.4 | pad only | dialogue: "Please stop messaging me" |
| 79.90 | light; pizzicato pulse + pad | 10_reply |
| 87.5–111.0 | pad only | WhatsApp dialogue |
| 112.44 | soft "ring ring" celesta accent; pad only | voice-call dialogue |
| 130.60 | medium; groove back | 12_verify |
| 140.5–149.1 | tremolo-string suspense pad | fake-payment dialogue |
| 149.63 | build | 13_learns |
| 158.10–159.50 | riser | |
| 159.72 | "Under the hood" hit: C minor, synth arpeggio, sub pulse, ticking hats, poly pad | t1_record |
| 167.50, 178.85, 188.17, 199.02, 208.77 | step accents; a layer is added at each (kick pulse, second arp, low strings + claps + taiko, ostinato, snare build) | t2–t6 |
| 218.76 | lift: B♭ – F/A – Gm – A, piano motif | 14_operator |
| 225.10–226.40 | big riser, then silence | |
| 226.72 | BIGGEST: braam, choir, full strings, horn theme | 15_proof |
| 235.33 | big hit, B♭ – C | 16_safe |
| 239.84 | final statement, sustained D major | 17_end |
| 244.70 | final low boom, hall tail to 247.665 | |

- **Measured:** 247.6650 s (10,922,027 frames; the M4A reads the same), −16.07 LUFS integrated, sample peak
  −1.04 dBFS, 0 clipped samples. The 1–4 kHz band is 1.6% of the energy (17.9 dB below full band).
  - RMS 1 s before / after: 26.28 s −20.4 → −10.4 dBFS (+10.0 dB); 71.35 s −21.0 → −16.4 dBFS (+4.6 dB,
    first 100 ms −10.4), then −43.6 dBFS over 72.5–74.4 s; 159.72 s −20.7 → −11.5 dBFS (+9.2 dB);
    226.72 s −16.4 → −10.7 dBFS (+5.7 dB; the second before holds the riser, then 0.3 s of silence).
  - Dialogue scenes: 74.6–79.4 s −28.2 dBFS RMS (−26.3 LUFS); 87.6–111.0 s −27.6 dBFS (−25.7 LUFS);
    112.5–130.1 s −28.2 dBFS (−26.4 LUFS); 140.6–149.1 s −27.7 dBFS (−25.9 LUFS). For comparison, the narrator
    bed in "looks" (32.9–47.8 s) is −17.3 dBFS and "Under the hood" is −17.1 dBFS.

## V3 short cut: `v3_trailer_short.wav` / `.m4a`

The same score cut to `audio/film_timeline_short.json` (119.287 s), with its own tempo map and hit map
(in `v3_hits_short.json`). It has no "Under the hood" section, so it stays in D minor and ends in D major.

| Time (s) | Hit / texture |
|---|---|
| 0.00 | cold-open boom, pizzicato and tick pulse |
| 17.30–18.27 | riser, then near-silence |
| 18.57 | BIG hit ("Meet Kairos") |
| 25.29 | medium |
| 30.30 | channel-ring flourish, galloping ostinato |
| 37.70 | soft "ring ring" accent; pad only under the call dialogue (37.8–48.7) |
| 49.16 | Aladdin: medium hit + brass stab |
| 56.94 | rules (light) |
| 64.00 | stop-hit, then near-silence to 67.2 |
| 67.2–81.2 | pad only under the stop and fake-proof dialogue; soft accent (boom, timpani, low strings) on the reveal at 75.75 |
| 81.68 | build |
| 90.58 | lift (dashboard) |
| 96.90–97.85 | riser, then silence |
| 98.14 | BIGGEST hit, choir |
| 106.95 | big hit |
| 111.46 | final statement, D major |
| 116.40 | final low boom, tail to 119.287 |

- **Measured:** 119.2870 s (5,260,557 frames), −16.02 LUFS integrated, sample peak −1.04 dBFS, 0 clipped samples;
  1–4 kHz band 1.9% of the energy. RMS 1 s before / after: 18.57 s −22.0 → −11.3 dBFS (+10.7 dB);
  49.16 s −30.4 → −13.8 dBFS; 64.00 s −23.6 → −17.2 dBFS, then −46.7 dBFS over 65.2–67.1 s;
  98.14 s −17.2 → −11.4 dBFS (+5.7 dB). Dialogue: 37.8–48.7 s −30.7 dBFS (−28.8 LUFS); 67.3–72.1 s −30.6 dBFS;
  72.7–81.2 s −25.9 dBFS (includes the reveal accent).

## V3 "Under the hood" (technical film): `v3_tech.wav` / `.m4a`

Scored to `audio/film_timeline_tech.json` (135.64 s, 13 narrator lines, no dialogue). This is the "Under the hood"
texture from the full film grown into a whole piece: an engineering-keynote bed under the narrator.

- **Tempo / key:** about 99 BPM (tempo map with every line start on a downbeat). C minor; a lift to E♭ major for the
  human-in-the-loop chapter; back to C minor; the proof climax ends on C major (A♭ – E♭ – Fm – G → C).
- **Instruments:** Synth Bass 2 39 (16th arpeggio), Electric Piano 2 5 (off-beat arp), Synth Bass 1 38 (sub pulse),
  Polysynth 90 and Halo 94 pads, two Harps 46 hard left and right with echoes (ping-pong plucks), String Ensemble 48
  ostinato + Pizzicato 45, Slow Strings 49, Trombone 57 / Tuba 58 / Brass 61 (Aladdin stab and the braams),
  Taiko 116, Timpani 47, GM kit (ticking hats, rim clicks, kick + synthesised thump, claps), Piano 0 (the "human"
  motif), French Horns 60 and Choir 52 (climax only), plus synthesised booms, sub drops, risers and ticks.

| Time (s) | Hit / texture | Line |
|---|---|---|
| 0.00–1.00 | soft riser | |
| 1.10 | cold-open hit (boom, sub drop, braam, C minor), halo pad, ticks | x01_intro |
| 4.31 | accent; arpeggio, sub pulse, poly pad, ticking hats | t1_record |
| 13.34 | accent; churn: the arpeggio reshuffles its notes each beat and re-sorts on the bar's last beat, rim clicks, kick pulse | x02_churn |
| 28.30 | accent; off-beat e-piano arp, halo pad, low strings, half-time kick | t2_model |
| 38.52 | Aladdin: low brass stab + braam, trombone pad, taiko | t3_reserve |
| 47.68 | accent; ping-pong harp plucks left/right with echoes, kick | t4_webhooks |
| 57.97 | accent; strings ostinato + pizzicato over the sub | x03_voice |
| 68.39 | accent; arps, e-piano, kick and claps (the decision ladder) | t5_intent |
| 78.86 | accent; build: everything + taiko, snare roll | x04_cm03 |
| 92.95–93.65 | riser | |
| 93.87 | lift to E♭ major: strings, piano motif (a person takes control), half-time | x05_hitl |
| 109.18 | accent; back to C minor, building | t6_loop |
| 118.40–119.05 | riser, then silence | |
| 119.34 | BIGGEST hit: braam, choir, strings, horn theme, taiko, arps | x06_proof |
| 128.54 | final statement: C major, sustained | x07_end |
| 133.30 | final low boom, tail to 135.64 | |

- **Measured:** 135.6400 s (5,981,724 frames; the M4A reads the same), −16.02 LUFS integrated, sample peak −1.04 dBFS,
  0 clipped samples; the 1–4 kHz band is 1.5% of the energy (18.2 dB below full band). The narrator bed 4.4–118.4 s
  averages −18.0 dBFS RMS (−17.0 LUFS). RMS 1 s before / after: 1.10 s −32.0 → −11.9 dBFS; 38.52 s −19.3 → −14.0;
  93.87 s −19.8 → −11.8 (+8.0 dB); 119.34 s −22.5 → −11.0 (+11.5 dB; the gap 119.07–119.33 s is −33.9 dBFS);
  128.54 s −14.1 → −11.1; the line-start accents add +3.4 to +6.8 dB.

## V1 "Uplifting": `v1_uplifting.wav` / `.m4a` (made for the earlier 139.6 s timeline, not re-rendered)

- **Tempo:** 112.4 BPM (intro 111.5 BPM so that bar 13 lands on the lift; a ritardando in the
  last climax bar puts the final chord on 131.31 s).
- **Key:** D major (intro coloured by B minor).
- **Instruments (GM):** Acoustic Grand Piano 0 (pulsing eighth chords, arpeggios with sustain pedal,
  and the climax melody in octaves), String Ensemble 48 + Slow Strings 49 (panned left and right),
  Synth Strings 50, Warm Pad 89, Fingered Bass 33, Glockenspiel 9 and Celesta 8 (with
  dotted-eighth delay), Choir Aahs 52 (low in the climax only), GM kit (kick, clap, side-stick,
  hats, shaker, toms, crash), Reverse Cymbal 119, plus a soft synthesised thump and noise riser
  at the lift and the climax.
- **Structure:**

| Time (s) | Section |
|---|---|
| 0.25–26.08 | Intro, 12 bars: Bm – Gmaj7 – Em7 – F♯m7 – A7sus4; solo piano, then strings; one repeated celesta "reminder" ping in every bar; side-stick tick and bass from bar 9; snare and riser into the lift |
| 26.08 | LIFT ("Meet Kairos"): crash, kick, bass and full D major; four-on-the-floor starts |
| 26.08–43.16 | A1: D – A/C♯ – Bm7 – Gmaj9 – D/F♯ – G – Em7 – Asus4 A; piano pulse, strings, soft kick and shaker; glockenspiel hook |
| 43.16–60.24 | A2: adds clap, hats and fuller bass |
| 60.24–77.32 | B: Gmaj7 – A – F♯m7 – Bm7 …; piano arpeggios (8ths and 16ths) |
| 77.32–94.40 | Dip: pad, softer kick, then the groove returns (under the Hindi line) |
| 94.40–111.49 | A3: full groove, open hats, octave bass, synth strings, hook again |
| 111.49–117.89 | Build: Em7 – F♯m7 – G A, snare roll, tom fill, riser |
| 117.89 | CLIMAX: G – A – F♯m7 – Bm7 – Gmaj7 – Asus4 A; full strings, choir, piano melody in octaves, glockenspiel |
| 131.31 | Final D add9 (piano, strings, pad, glockenspiel motif), with the natural hall tail to 139.6 |

- **Measured:** −16.0 LUFS integrated, sample peak −1.04 dBFS, 0 clipped samples, 139.6350 s.
  1–4 kHz band is 2.3% of the energy (16.4 dB below full band). RMS 1 s before / 1 s after:
  lift 26.08 s −21.4 → −15.5 dBFS (+5.9 dB); climax 117.89 s −15.7 → −13.8 dBFS (+2.0 dB; the
  second before is the snare roll and riser; 3 s short-term loudness goes from about −16 to −12.5 LUFS);
  final 131.31 s −15.6 → −13.1 dBFS.

## V2 "Modern tech": `v2_modern_tech.wav` / `.m4a` (made for the earlier 139.6 s timeline, not re-rendered)

- **Tempo:** 117.6 BPM (intro 119.6 BPM so that bar 14 lands on the lift).
- **Key:** A minor intro, C major from the lift.
- **Instruments (GM):** Electric Piano 4 (syncopated stabs, held chords, hook), Electric Guitar
  (muted) 28 as a 16th-note pluck with dotted-eighth delay, Synth Bass 38 (eighths and octaves),
  Warm Pad 89 and Synth Strings 50 with sidechain-style pumping, Celesta 8 (hook, with delay),
  GM kit (kick layered with a synthesised sub thump, claps, snare, closed and open hats, crash),
  Reverse Cymbal 119, synthesised riser.
- **Structure:**

| Time (s) | Section |
|---|---|
| 0.00–26.08 | Intro, 13 bars: Am – Fmaj7 – Dm7 – Esus4 E – … – Gsus4 G; e-piano, pluck from bar 3, pad from bar 5, ticking hats, one repeated celesta "reminder" figure in every bar |
| 26.08 | LIFT into C major: crash, kick, synth bass, thump |
| 26.08–42.40 | A1: C – G/B – Am7 – Fmaj7 – C – G – Fmaj7 – Gsus4 G; four-on-the-floor, claps, e-piano stabs, celesta hook |
| 42.40–58.72 | A2: 16th hats, octave bass, pluck arpeggio |
| 58.72–75.05 | B: Fmaj7 – G – Em7 – Am7 …; syncopated kick, synth strings |
| 75.05–91.37 | Dip: claps and hats only, then a half-time kick (under the Hindi line) |
| 91.37–107.69 | A3: full groove, open hats, e-piano hook |
| 107.69–117.89 | Build: Fmaj7 – G – Am7 – Fmaj7 – Gsus4 G, snare roll, riser |
| 117.89 | CLIMAX: F – G – Em7 – Am7 – F – G; everything, the hook up an octave on e-piano and celesta |
| 130.13–131.31 | Drop-out (in the voice gap) with reverse cymbal |
| 131.31 | Final C add9 hit, held, with the tail to 139.6 |

- **Measured:** −16.0 LUFS integrated, sample peak −1.04 dBFS, 0 clipped samples, 139.6350 s.
  1–4 kHz band is 1.0% of the energy (20.2 dB below full band). RMS 1 s before / 1 s after:
  lift 26.08 s −20.4 → −15.3 dBFS (+5.1 dB); climax 117.89 s −16.6 → −13.6 dBFS (+3.0 dB);
  final 131.31 s −25.9 → −13.8 dBFS (+12.1 dB, after the drop-out).

## Limits

The Apple GM DLS is a ~2 MB General MIDI set: the strings, choir and brass are single-layer
samples without round-robins or real articulations, so they read as "good demo", not as a
recorded orchestra. The trailer weight comes mostly from the synthesised booms, sub drops and
the saturated brass/saw braam layer. For a release mix, the same note data could be rendered
through a professional orchestral library.
