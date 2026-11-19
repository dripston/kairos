# Kairos films (v3): QA

Built {{BUILT}} from branch `film/walkthrough` at {{COMMIT}}. Everything shown is **SIMULATED**: synthetic borrowers, local servers (plus the read-only deployed dashboard), and dry-run adapters. Nothing was sent to anyone, and no real call was placed.

| | Product film, "Kairos in action" | Technical film, "How Kairos works" |
|---|---|---|
| Master | `{{OUTDIR}}/kairos_product.mp4` | `{{OUTDIR}}/kairos_technical.mp4` |
| Copy | `{{OUTDIR}}/kairos_product_720p.mp4` | `{{OUTDIR}}/kairos_technical_720p.mp4` |
| Captions (sidecars) | `kairos_product.srt` / `.vtt` | `kairos_technical.srt` / `.vtt` |
| Master check (AVAsset reader) | {{P_FINISH}} | {{T_FINISH}} |
| `afinfo` | {{P_AFINFO}} | {{T_AFINFO}} |

## Gate checklist

| Check | Product | Technical | How it was checked |
|---|---|---|---|
| Captions on every spoken second | {{P_CC}} | {{T_CC}} | `tools/captions.py` builds the cues from the mix's own line timings, and exits non-zero if any spoken line is uncovered. The same cues are burned in (46 px, white on a 74% dark pill, speaker tag in teal) and written as `.srt` and `.vtt`. Cues are whole sentences, or explicit complete phrases for the two long lines; 2 lines max, about 42 characters per line (44 max); line breaks fall after a clause mark or before a conjunction. Dialogue cues carry the speaker tag on every cue. |
| Human → screen → human | {{P_RUN}} | {{T_RUN}} | The longest run of consecutive shots with no person in them, counted from the film data (`tools/qa_doc.py`). The rule is at most 2 cream cards back to back. |
| Text is large enough | ✅ | ✅ | Captions 46 px; headlines 72–150 px; tags, fine print and credits 32 px. Real UI is shown as one component per card, scaled so 13 px UI text renders at 28–47 px (see the per-5 s tables). Phone and laptop screens in footage are scene detail; the readable component always follows on a card. |
| No text over UI | ✅ {{P_QA}} | ✅ {{T_QA}} | `render --qa` walks each film at 10 fps and fails if a callout overlaps the focused subject or unfocused UI, or a caption overlaps a card. Cards sit above the caption band; tags sit on cream above the card. |
| Real product in every product beat | ✅ | ✅ | Every phone, laptop and monitor screen is a real capture composited onto the footage (`tools/screen_comp.swift`, Core Image chroma or white-screen key, perspective tracking). Every card is a crop of a real capture (`tools/record.swift`, offscreen WKWebView). Terminal lines are recorded output. |
| Music: licensed, cut on phrases, never stretched | ✅ | ✅ | **Product:** "Corporate Inspirational" (AtlasAudio, Pixabay licence), 0 → 116.82 s, the bar 68 downbeat that ends the outro's first 4-bar phrase (140 BPM). **Technical:** "Inspiring Technology" (The_Mountain, Pixabay licence), 0 → 108.02 s, the bar 36 downbeat (80 BPM). Each ends with a 25 ms hard stop into the silent lockup. No internal splices, no time-stretch. Bars from `tools/beats.swift`. |
| Narration: 10 lines or fewer | ✅ {{P_VO}} | ✅ {{T_VO}} | Sarvam bulbul:v3, Neha, pace 1.2 (`audio/vo/`). Characters: Ritu (Kairos) and Pooja (Sunita), en-IN, pace 1.05 (`audio/dlg/`); the technical film's voice call is the earlier Hindi recording, captioned in English. Vikram's Hindi WhatsApp reply is on screen only, captioned "I'll pay by the 29th." |
| Loudness | {{P_LUFS}} | {{T_LUFS}} | BS.1770 integrated loudness and true peak on the mix (`tools/mix`), then again on the master's own AAC track (`../tools/score_analyze.swift`, sample peak). Music ducks 18 dB under every voice. |
| No empty frames, no dead seconds | {{P_EMPTY}} | {{T_EMPTY}} | `tools/qa_frames` samples every 1 s; it fails on a frame more than 97% uniform, and flags a second identical to the next. |

## Claims on screen and in narration

| Claim | Where | Source | Label in film |
|---|---|---|---|
| Fixed schedule: 1,262 messages → 23 replies | product hook | `/demo/live/state` after the seed-42 run (`counters.cn10`) | "Fixed schedule · SIMULATED run, seed 42" |
| About twice the money back per ₹ | product N10 and proof card | `backend/README.md` final unseen seeds; `film/TRUTH_TABLE.md` | "In our tests"; "vs a fixed-schedule stand-in"; "SIMULATED · synthetic borrowers · 3 unseen seeds · same budget and caps" |
| 0 rule breaches | product N10 and proof card | PROGRESS §1; `/demo/live` "Rule breaches 0" | SIMULATED |
| 49/49 attacks blocked | product proof card; technical terminal | real `run_redteam.py` output | "on our own red-team suite" |
| Identity first (OTP), fake screenshot caught, STOP on every channel | product Sunita beats | real `/demo/journey/inbound` captures (engine times 0.7–4.0 ms) | the page's own labels |
| Vikram: SMS 9:30 → no reply → WhatsApp → Hindi promise → paid ₹7,409 | product Vikram beats | real `/demo/journey/outbound` captures | Day / time stamps from the page; SIMULATED |
| Approval before a voice call; an override at 20:00 is blocked | product Asha beats | real outbound captures (Aarav) | "Locked by guardrails: no person can override this." |
| Churn re-sorts borrowers daily; every move names its rule | technical | Engine Room (`demo/engine-room` @ c3abbd8), K27 churn panel and `&inspect=1` rule card | "Engine Room · SIMULATED · seed 42 · 300 synthetic borrowers · 14 days" |
| Day 7: WhatsApp replies drop to about a third; Kairos is not told | technical | Engine Room `?at=6.05` (the simulated world change, ×0.35) | same tag; the page's own callout |
| End of day 14: the 1–30-days-late segment moved to SMS, learned from replies | technical | Engine Room `?at=13.98` | same tag; the page's own callout |
| Voice dispositions with confidence; below a hand-set threshold a person reviews | technical | deployed dashboard Voice bots page (read-only); `policy/K17-voice-dispositions/thresholds.json` (0.5–0.8) | "hand-set" |
| Same seed twice: IDENTICAL; 50,000 atomic decisions in 11.1 s, 0 cap breaches; 3030 passed; OPE 18.2 vs truth 18.4 | technical terminal | real recorded runs (`term/*.json`) | speed-up badges; "SIMULATED" on OPE |
| Built on research (10 citations) | technical card | `research/papers.md` §B | "Built on research" |

Wording rules kept: "Bayesian learner" (never Thompson sampling, RL or cost-optimal); thresholds are "hand-set"; "pause outbound", not per-campaign; SMS and WhatsApp are dry-run; 9.9× / 15.9× never shown.

## Frame check every 5 s

Thumbnails from the finished masters: `qa/kairos_product_every5s.jpg` and `qa/kairos_technical_every5s.jpg`. The tables are generated from the film data at the same timestamps (`render --describe`, `tools/qa_table.py`).

### Product film

{{PRODUCT_TABLE}}

### Technical film

{{TECH_TABLE}}

## Honest notes (what is not perfect)

{{NOTES}}

## Rebuild

```sh
cd film/walkthrough
python3 tools/shots_product.py                     # footage + real UI → comp/*.mov
python3 tools/build_product3.py && python3 tools/build_technical3.py
./tools/mix mix/product3.json && ./tools/mix mix/technical3.json
python3 tools/captions.py audio/product3_cues_raw.json audio/product3_cues.json ../out/kairos_product audio/product3_cues_extra.json
python3 tools/captions.py audio/technical3_cues_raw.json audio/technical3_cues.json ../out/kairos_technical
bash tools/final.sh product3 kairos_product ../out && bash tools/final.sh technical3 kairos_technical ../out
python3 tools/qa_doc.py ../out
```

Captures: `tools/record` with `scripts/*.json` against a local server (isolated `KAIROS_DB`, `KAIROS_CHURN_DB`, `KAIROS_CM03_DEMO_INBOX=1`), the read-only deployed dashboard, and the Engine Room. Footage: `footage_manifest/fetch_human.sh` (Pexels; not committed). Music: `../music/licensed/fetch_music.sh` (Pixabay; not committed). Narration: `tools/vo.py` and `tools/dlg.py` (the Sarvam key is read from `backend/.env` and never printed).
