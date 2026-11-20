# Kairos films (v4 finals): QA

Built 20 Nov 2026 14:00 IST from `film/walkthrough` at 30f470e. Everything shown is **SIMULATED**: synthetic borrowers, the release-candidate server (port 8120) and the deployed dashboard read-only, and dry-run adapters. Nothing was sent to anyone.

**How they are made**
- **4K:** the 1920×1080 CSS stage is re-painted by WebKit at 3840×2160 (not upscaled). H.264 High, 48 Mb/s, BT.709, 30 fps. The 1080p file is a downscale of the 4K master.
- **Captures:** every product page is a capture at device scale 4. Zooms are capped at 2× of the page, so captures are never upscaled. The F02/F03 dashboard recordings are 1080p video, shown at ≤ 1× of their pixels.
- **Windows:** a window shows a whole page, then animates to a whole component (DOM rect + margin, header row included). It never holds empty fill and never cuts a glyph.
- **Footage:** retimed so every output frame maps to exactly one source frame (25 fps at 1.2×, 24 fps at 1.25×).
- **Nebula:** always plays in real time (ping-pong on long spans).
- **Rendering:** each frame is snapshotted only after its video frames are presented and its paint is committed.
- **Captions:** each cue starts at its clip's measured speech onset or at the end of the real pause nearest the phrase boundary.

### Product film, “Kairos in action”

| | |
|---|---|
| 4K master | `film_out/kairos_product_final_4k.mp4` · 220.000 s, 3840x2160 @ 30.00 fps, 6600 frames decoded (reader completed), audio tracks 1 (219.952 s), last frame ok, 1308.3 MB |
| 1080p | `film_out/kairos_product_final_1080p.mp4` · 220.000 s, 1920x1080 @ 30.00 fps, 6600 frames decoded (reader completed), audio tracks 1 (219.952 s), last frame ok, 289.9 MB |
| Captions | `film_out/kairos_product_final.srt` / `.vtt` · 50 cues, ≤ 2 lines, ≤ 44 characters per line, burned in |
| Narration | 18 Neha lines (pace 1.3), 7 dialogue lines (Kavya 1.05 / Tanya 1.0) |
| Mix | integrated -14.10 LUFS (target -14.0)  true peak -1.04 dBTP  duration 220.01 s · voice on 174.8 s = 79% of runtime, 25 lines · music under voice -26.1 dB vs in gaps -12.9 dB (duck 18 dB) |

Every-frame analysis of the 1080p (`qa_tools/frameqa` + `analyze.py`):

```
out/kairos_product_final_rs_frames.csv: 6600 frames, 220.0 s, 30.0 fps
groups: {'FLICKER': 8, 'STUTTER': 19}
    10.13          FLICKER  x1   tile top-left r=0.18 d=5.6/6.3
    10.70          FLICKER  x1   tile top-right r=0.24 d=9.4/7.3
    11.07-  11.13  FLICKER  x2   tile top-midL r=0.22 d=8.3/6.4
    14.67          FLICKER  x1   tile upper-midR r=0.12 d=7.9/8.1
    36.47          STUTTER  x1   motion 0.89 -> freeze 0.014 -> 0.90
    41.10          STUTTER  x1   motion 0.79 -> freeze 0.014 -> 0.91
    46.63          STUTTER  x1   motion 1.66 -> freeze 0.018 -> 1.67
    52.37          STUTTER  x1   motion 1.64 -> freeze 0.013 -> 1.59
    59.73          STUTTER  x1   motion 1.27 -> freeze 0.019 -> 1.27
    73.70          STUTTER  x1   motion 1.38 -> freeze 0.018 -> 1.35
    75.53          STUTTER  x1   motion 1.47 -> freeze 0.014 -> 1.48
    90.00          STUTTER  x1   motion 0.95 -> freeze 0.012 -> 0.96
    90.93          STUTTER  x1   motion 1.05 -> freeze 0.012 -> 1.05
    96.80          FLICKER  x1   tile upper-midR r=0.20 d=1.5/2.1
   104.07          FLICKER  x1   tile bottom-midR r=0.18 d=1.8/1.8
   104.63          FLICKER  x1   tile lower-midL r=0.11 d=1.2/1.1
   104.77          STUTTER  x1   motion 0.62 -> freeze 0.016 -> 0.63
   109.40          STUTTER  x1   motion 0.98 -> freeze 0.012 -> 0.97
   111.57          FLICKER  x1   tile lower-midR r=0.15 d=57.2/64.0
   138.33          STUTTER  x1   motion 1.10 -> freeze 0.008 -> 1.09
   142.93          STUTTER  x1   motion 1.51 -> freeze 0.007 -> 1.63
   148.67          STUTTER  x1   motion 1.49 -> freeze 0.017 -> 1.80
   149.60          STUTTER  x1   motion 1.38 -> freeze 0.016 -> 1.36
   152.37          STUTTER  x1   motion 1.15 -> freeze 0.016 -> 1.16
   208.27          STUTTER  x1   motion 1.64 -> freeze 0.018 -> 1.59
   209.77          STUTTER  x1   motion 1.56 -> freeze 0.013 -> 2.39
   211.03          STUTTER  x1   motion 1.68 -> freeze 0.019 -> 1.70
```

### Technical film, “How Kairos works”

| | |
|---|---|
| 4K master | `film_out/kairos_technical_final_4k.mp4` · 204.033 s, 3840x2160 @ 30.00 fps, 6121 frames decoded (reader completed), audio tracks 1 (203.972 s), last frame ok, 1210.0 MB |
| 1080p | `film_out/kairos_technical_final_1080p.mp4` · 204.033 s, 1920x1080 @ 30.00 fps, 6121 frames decoded (reader completed), audio tracks 1 (203.972 s), last frame ok, 268.0 MB |
| Captions | `film_out/kairos_technical_final.srt` / `.vtt` · 41 cues, ≤ 2 lines, ≤ 44 characters per line, burned in |
| Narration | 10 Neha lines (pace 1.3), 0 dialogue lines (Kavya 1.05 / Tanya 1.0) |
| Mix | integrated -14.05 LUFS (target -14.0)  true peak -1.04 dBTP  duration 204.02 s · voice on 179.6 s = 88% of runtime, 10 lines · music under voice -24.0 dB vs in gaps -9.0 dB (duck 18 dB) |

Every-frame analysis of the 1080p (`qa_tools/frameqa` + `analyze.py`):

```
out/kairos_technical_final_tf_frames.csv: 6121 frames, 204.0 s, 30.0 fps
groups: {'STUTTER': 23, 'FLICKER': 1}
    11.17          STUTTER  x1   motion 1.64 -> freeze 0.017 -> 1.65
    13.93          STUTTER  x1   motion 2.17 -> freeze 0.019 -> 2.23
    17.27          STUTTER  x1   motion 1.85 -> freeze 0.015 -> 1.85
    17.83          STUTTER  x1   motion 1.83 -> freeze 0.013 -> 1.87
    41.93          STUTTER  x1   motion 0.99 -> freeze 0.012 -> 0.97
    42.83          STUTTER  x1   motion 1.00 -> freeze 0.011 -> 0.99
    49.30          STUTTER  x1   motion 1.61 -> freeze 0.012 -> 1.68
    49.87          STUTTER  x1   motion 1.68 -> freeze 0.007 -> 1.85
    57.80          STUTTER  x1   motion 1.46 -> freeze 0.016 -> 1.49
    62.43          STUTTER  x1   motion 1.01 -> freeze 0.015 -> 1.00
    74.53          STUTTER  x1   motion 1.20 -> freeze 0.012 -> 1.17
    79.13          STUTTER  x1   motion 2.06 -> freeze 0.017 -> 2.12
    81.90          STUTTER  x1   motion 1.86 -> freeze 0.016 -> 1.86
    82.47          STUTTER  x1   motion 1.94 -> freeze 0.009 -> 1.92
    84.87          STUTTER  x1   motion 1.99 -> freeze 0.019 -> 1.98
    90.40          STUTTER  x1   motion 1.69 -> freeze 0.018 -> 1.70
    95.03          STUTTER  x1   motion 0.65 -> freeze 0.012 -> 0.64
   115.63          STUTTER  x1   motion 1.10 -> freeze 0.015 -> 1.09
   121.17          STUTTER  x1   motion 1.58 -> freeze 0.011 -> 1.60
   140.63          STUTTER  x1   motion 1.62 -> freeze 0.010 -> 1.62
   144.33          STUTTER  x1   motion 1.83 -> freeze 0.014 -> 1.87
   145.27          STUTTER  x1   motion 1.73 -> freeze 0.016 -> 1.73
   164.30          STUTTER  x1   motion 1.04 -> freeze 0.011 -> 1.15
   172.40          FLICKER  x1   tile upper-midR r=0.10 d=0.8/1.0
```

## Honest notes

**Length**
- The user chose about 3:30 per film. Neha stays at pace 1.3, and time was cut only from words and holds.
- With the approved trimmed lines, the voice alone is about 186 s in the product film (narration plus dialogue) and 180 s in the technical film.
- The product film lands at about 3:46. It also carries a cold open, a logo, two part cards, the Aladdin flash, three slogans and the lockup.
- The technical film lands at about 3:24.

**Footage and screens**
- **Stock people:** every person is Pexels stock, cast per `CASTING.md`: Vikram is the Delhi man (H2_4 and H7_6), and the montage and lender side use the human2 batch. They are actors, not users.
- **No tracked composites:** no UI is composited onto a moving phone or laptop. Every real screen is a still or recorded page in a glass window, or a still phone frame (`comp/phone.html` renders of the real message texts).
- **Whole pages:** pages are shown whole first. Where a browser viewport ends mid-page (the journey and inbound pages are 1440×810 and 1440×1150 viewports), the glass window shows the viewport the app shows. For the Control Room, the Engine Room and Voice bots, the page rectangle is chosen so that every card on screen is complete.
- **Engine Room animations:** the recorder freezes page animations, so the Engine Room's pulse and chip flip are not in the film; the stills show the final states.

**Data and wording**
- **Mixed runs:** the journey cards are from the release-candidate server (8120) and were matched to the story by content, not by step index (the indices drift between runs). The Engine Room run (seed 42, 300 borrowers, 14 days) and the journeys are different SIMULATED runs.
- **73% resolved without a person:** from 1,000 seeded test conversations. Counting only bank-verified payments (not recorded claims), the rate is **67.3%** (backend/README.md). That caveat is kept here, not on screen.
- **No vendor names:** the word "Sarvam" appears nowhere. The Voice bots page is shown only from the Calls card down; its header names the vendor. The deployed frontend's Control Room and Learning & Proof pages are cropped to cards without it.
- **The "presenter" wording** on one journey approval card is not used. The approval is shown from the F02 dashboard recording instead.

**Sound**
- **Music length:** both tracks are extended by repeating whole phrases (32, 16 or 8 bars, at downbeats, with 20–30 ms joins). They end on their own outro and ending, with no time-stretch.
- **Voices:** Neha (narration, pace 1.3, "Alaadin" in the TTS text), Kavya (the agent, 1.05) and Tanya (the borrower, 1.0), all AI-generated.

## Fixes after the final review (20 Nov, 13:08–13:59 IST)

- **Product** (re-spliced into the reviewed 4K master at 13:08):
  - At 1:51.57 the glass now fades in from the nebula (no empty glass frame).
  - Every glass-to-glass dissolve now eases over 0.6 s: the new window fades in over 0.4 s while the old one fades out over 0.1–0.6 s.
  - The identity, fake-proof and STOP beats hold on the result card (no phone-to-card snap).
  - The first splice at 12:47 had 2 bare-background frames at each range start. It was withdrawn. Range renders now use `--preroll 1.0`.
- **Technical** (re-spliced into the v4e 4K master at 13:57): all 21 transitions into or out of a title, chapter, big-number or brand card are sequential dips. The old element fades out over 0.25 s, then the card fades in over 0.25 s; texts never overlap. Window, diagram and panel crossfades are unchanged.
- **Still in both films:** single duplicated frames from the N1 nebula source (STUTTER flags). About 1 frame per second shows during nebula backgrounds.
  - The final2 attempt dropped those frames (`comp/NEB1c.mov`, `shots/NEB1c.json`). It was rejected: the technical final2 dropped to flat dark for one frame about every 31.3 s, which is the ping-pong period of the shorter clip.
  - The product final2 render timed out on a seek. NEB1c is not used.
