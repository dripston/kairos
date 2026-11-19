# Kairos walkthrough films: QA

Built {{BUILT}} on branch `film/walkthrough`. Everything shown is **SIMULATED**: synthetic borrowers, local servers, and dry-run adapters. Nothing was sent to anyone.

| | Product film, "Kairos in action" | Technical film, "How Kairos works" |
|---|---|---|
| Master | `film/out/kairos_product.mp4` | `film/out/kairos_technical.mp4` |
| Copy | `film/out/kairos_product_720p.mp4` | `film/out/kairos_technical_720p.mp4` |
| Duration (AVAsset) | {{P_DUR}} | {{T_DUR}} |
| Video | {{P_VID}} | {{T_VID}} |
| Audio | AAC 256 kb/s, 44.1 kHz stereo, {{P_AUD}} | AAC 256 kb/s, 44.1 kHz stereo, {{T_AUD}} |
| Plays start to finish | {{P_PLAY}} | {{T_PLAY}} |
| `afinfo` duration | {{P_AFINFO}} | {{T_AFINFO}} |

## Gate checklist

| Check | Product | Technical | How it was checked |
|---|---|---|---|
| Every readable element is large enough | ✅ | ✅ | See the per-5 s tables below. **Headlines** are 72–112 px and **overlays** 72 px (result lift-outs 50 px mono). **Tags, fine print and credits** are 32 px. **Captured UI** is always in focus at a 1.9–2.45× zoom, so 13 px UI text renders at 28–36 px and 16 px text at 35–45 px. **Terminal** mono is 33 px. **Diagram** titles are 60–70 px when in focus, and 36 px in the full overview at 0.74×. Wide shots of a whole page are shown only while a window enters or a cut-in grows out of its node, and last 1.5 s or less. Exceptions are listed under *Honest notes*. |
| No repeated text | ✅ | ✅ | No overlay repeats a narration sentence, and no overlay repeats the UI line under it (overlays name the idea; the UI shows the detail). The end line is on screen only and never spoken. One deliberate echo: the product proof card states ≈2× and 0 breaches while narration line N10 says them in other words, because critical claims belong in text plus voice. |
| Overlays never sit on live text | ✅ 0 collisions | ✅ 0 collisions | `render --qa` walks each film at 10 fps. It fails if a callout overlaps the focused subject or sits over unfocused live UI. **Focus mode** blurs (12 px) and veils (72% cream) everything except the component being discussed. While a terminal result is lifted out, the terminal behind it blurs. |
| Every scene shows the real product or a real artifact | ✅ | ✅ | Every product picture is a capture of the running pages, made by `tools/record.swift` in an offscreen WKWebView with scripted clicks and typing. Every terminal line is recorded output (`tools/termrec.py`, `term/*.json`). Non-product shots: one 3.7 s establishing shot (Mumbai at night, Pexels), the hook's unit chart of the real seed-42 counts, chapter headlines, the proof card, the research card, and the lockups. |
| Music is the licensed track, cut on phrases | ✅ | ✅ | **Product:** "Corporate Inspirational" (AtlasAudio, Pixabay), played 0 → 109.93 s, the end of its lift phrase (140 BPM, bar 1.714 s). **Technical:** "Inspiring Technology" (The_Mountain, Pixabay), 0 → 108.0 s, the bar 36 downbeat (80 BPM, bar 3.0 s). Each ends with a 25 ms hard stop into silence under the lockup. There are no internal splices and no time-stretch. Bars were found with `tools/beats.swift`. |
| Narration has 10 lines or fewer | ✅ 10 lines, 99 words, voice {{P_VOICE}} | ✅ 9 lines, 135 words, voice {{T_VOICE}} | Sarvam bulbul:v3, Neha, pace 1.2 (`audio/vo/`, `tools/vo_lines.json`) |
| Loudness | {{P_LUFS}} | {{T_LUFS}} | BS.1770-4 integrated loudness, measured on the mix and again after AAC encoding (`../tools/score_analyze.swift`). Music is ducked 18 dB under the voice (−34 / −29 dB under the voice vs −16 / −12 dB in the gaps). |
| No empty frames | {{P_EMPTY}} | {{T_EMPTY}} | `tools/qa_frames` samples every 1 s and fails on any frame more than 97% uniform |
| No dead, identical seconds | {{P_STILL}} | {{T_STILL}} | Same tool: mean frame difference to 1 s later must be ≥ 0.15 levels |

## Claims on screen and in narration

The full claim map is in `STORYBOARD.md`. Summary:

| Claim | Where | Source | Label in film |
|---|---|---|---|
| Fixed schedule: 1,262 messages → 23 replies | product hook | `/demo/live/state` after the seed-42 run (`counters.cn10`) | "Fixed schedule · SIMULATED run, seed 42" |
| ≈2× money back per ₹ spent | product proof card, N10 | `backend/README.md` final unseen seeds; `film/TRUTH_TABLE.md` | "vs a fixed-schedule stand-in"; "SIMULATED · 3 unseen seeds · same budget and caps"; "In our tests" |
| 0 rule breaches | product proof card and STOP card capture | PROGRESS §1; real `/demo/live` card "Rule breaches 0" | SIMULATED |
| 49/49 attacks | product proof card; technical terminal | real `run_redteam.py` output, 20 Nov | "on our own red-team suite" |
| 50,000 atomic decisions in 11.1 s, cap breaches 0 | technical terminal | real `bench_ledger.py 50000` output | "ledger benchmark" with its real speed-up badge |
| 3030 passed | technical terminal | real `pytest` run on kairos-main 0778ce8 | — |
| Deterministic replay: IDENTICAL | technical terminal | real `run_k09_replay.py … --check-determinism` (638 s) | "638 s run · shown 600× faster" |
| OPE: from logs 18.2, truth 18.4 | technical terminal (CN10 row highlighted) | real `ope_k09.py`, `research/calibration-ope` 3d02d2c | "SIMULATED", "CN10 fixed schedule" |
| Bayesian learner, per segment and per borrower; thresholds hand-set, fail closed | technical narration | `research/papers.md` §D | never "Thompson", "RL" or "calibrated" |
| Built on research (10 citations) | technical card | `research/papers.md` §B; `backend/README.md` Research lineage | "Built on research" |

## Frame check every 5 s

Thumbnails: `qa/product_every5s.jpg` and `qa/technical_every5s.jpg`, made from the finished MP4s. The tables below are generated from the film data at the same timestamps (`render --describe`, `tools/qa_table.py`). The sizes are what a 13 px / 16 px UI label renders at in the 1080p frame.

### Product film

{{PRODUCT_TABLE}}

### Technical film

{{TECH_TABLE}}

## Honest notes (what is not perfect)

**Legibility exceptions**
- **Wide shots:** the window entrances and the match-cut growth into a cut-in briefly show a whole page with UI text at 15–18 px. These shots are never held to be read and last 1.5 s or less.
- **Fast-forwards:** in the product film the pipeline strip is shown at 1.9× (13 px labels → 28 px) while steps fast-forward.
- **Diagram overview:** at 0.74×, subtitles render at 27 px. The titles (36 px) carry the meaning.
- **Phone:** WhatsApp text renders at 31 px at a 1.95× zoom.

**Captures and timing**
- **Step-index drift:** the outbound journey page's step index drifts by one or two between runs. That is why the Day 3 learner decision shows as the pipeline box "Contact: WhatsApp at 09:30" rather than its own event card.
- **Rules page:** after a full-system-trace run, the /demo/live Rules page shows all counters at 0. That looks like a page bug, so the film does not use that page. The results beat uses the Overview's STOP / "Rule breaches 0" card and the System page instead.
- **Speed-ups:** terminal runs are sped up (badges say by how much). The determinism run took 638 s.
- **Recorder animations:** an offscreen WKWebView does not advance CSS animations, so the recorder disables page animations. The pages' own micro-animations are therefore not in the film; the compositor supplies all motion.

**Frame rate and audio**
- **Frame rate:** both masters are 60 fps. Every frame is rendered, not interpolated. Capture bursts (the system trace and the console tour) are recorded at 4 fps of real page updates; the counters change in discrete steps there, as they do on the page.
- **Music:** the product track's last phrase ends 5 s before the film does. The lockup holds in silence by design ("hard stop to lockup").

## Rebuild

```sh
sh film/walkthrough/tools/build.sh 60
```

Captures are made with `tools/record` and `scripts/*.json` against a local server from `demo/journeys` (isolated `KAIROS_DB`, `KAIROS_CHURN_DB`, and `KAIROS_CM03_DEMO_INBOX=1`). Terminal recordings use `tools/termrec.py`. Narration uses `tools/vo.py` (the key is read from `backend/.env`, never printed).
