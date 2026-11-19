# Kairos films (v3): QA

Built 20 Nov 2026 05:14 IST from branch `film/walkthrough` at ab9a8be. Everything shown is **SIMULATED**: synthetic borrowers, local servers (plus the read-only deployed dashboard), and dry-run adapters. Nothing was sent to anyone, and no real call was placed.

| | Product film, "Kairos in action" | Technical film, "How Kairos works" |
|---|---|---|
| Master | `film_out/kairos_product.mp4` | `film_out/kairos_technical.mp4` |
| Copy | `film_out/kairos_product_720p.mp4` | `film_out/kairos_technical_720p.mp4` |
| Captions (sidecars) | `kairos_product.srt` / `.vtt` | `kairos_technical.srt` / `.vtt` |
| Master check (AVAsset reader) | 121.417 s, 1920x1080 @ 60.00 fps, 7285 frames decoded (reader completed), audio tracks 1 (121.369 s), last frame ok, 227.1 MB | 112.617 s, 1920x1080 @ 60.00 fps, 6757 frames decoded (reader completed), audio tracks 1 (112.569 s), last frame ok, 195.2 MB |
| `afinfo` | 121.438912 s | 112.638549 s |

## Gate checklist

| Check | Product | Technical | How it was checked |
|---|---|---|---|
| Captions on every spoken second | ✅ 21 cues, 0 uncovered lines, ≤ 2 lines, longest line 44 chars, sidecars written | ✅ 20 cues, 0 uncovered lines, ≤ 2 lines, longest line 43 chars, sidecars written | `tools/captions.py` builds the cues from the mix's own line timings, and exits non-zero if any spoken line is uncovered. The same cues are burned in (46 px, white on a 74% dark pill, speaker tag in teal) and written as `.srt` and `.vtt`. Cues are whole sentences, or explicit complete phrases for the two long lines; 2 lines max, about 42 characters per line (44 max); line breaks fall after a clause mark or before a conjunction. Dialogue cues carry the speaker tag on every cue. |
| Human → screen → human | ✅ longest run 2 (at 18.0 s) | ✅ longest run 2 (at 5.2 s) | The longest run of consecutive shots with no person in them, counted from the film data (`tools/qa_doc.py`). The rule is at most 2 cream cards back to back. |
| Text is large enough | ✅ | ✅ | Captions 46 px; headlines 72–150 px; tags, fine print and credits 32 px. Real UI is shown as one component per card, scaled so 13 px UI text renders at 28–47 px (see the per-5 s tables). Phone and laptop screens in footage are scene detail; the readable component always follows on a card. |
| No text over UI | ✅ 0 collisions | ✅ 0 collisions | `render --qa` walks each film at 10 fps and fails if a callout overlaps the focused subject or unfocused UI, or a caption overlaps a card. Cards sit above the caption band; tags sit on cream above the card. |
| Real product in every product beat | ✅ | ✅ | Every phone, laptop and monitor screen is a real capture composited onto the footage (`tools/screen_comp.swift`, Core Image chroma or white-screen key, perspective tracking). Every card is a crop of a real capture (`tools/record.swift`, offscreen WKWebView). Terminal lines are recorded output. |
| Music: licensed, cut on phrases, never stretched | ✅ | ✅ | **Product:** "Corporate Inspirational" (AtlasAudio, Pixabay licence), 0 → 116.82 s, the bar 68 downbeat that ends the outro's first 4-bar phrase (140 BPM). **Technical:** "Inspiring Technology" (The_Mountain, Pixabay licence), 0 → 108.02 s, the bar 36 downbeat (80 BPM). Each ends with a 25 ms hard stop into the silent lockup. No internal splices, no time-stretch. Bars from `tools/beats.swift`. |
| Narration: 10 lines or fewer | ✅ 10 Neha lines, 99 words | ✅ 10 Neha lines, 160 words | Sarvam bulbul:v3, Neha, pace 1.2 (`audio/vo/`). Characters: Ritu (Kairos) and Pooja (Sunita), en-IN, pace 1.05 (`audio/dlg/`); the technical film's voice call is the earlier Hindi recording, captioned in English. Vikram's Hindi WhatsApp reply is on screen only, captioned "I'll pay by the 29th." |
| Loudness | ✅ mix -14.01 LUFS, true peak -1.04 dBTP; music -31.0 dB under voice vs -14.1 dB in gaps; after AAC -14.03 LUFS, sample peak -1.38 dBFS | ✅ mix -14.07 LUFS, true peak -1.04 dBTP; music -28.2 dB under voice vs -11.6 dB in gaps; after AAC -14.08 LUFS, sample peak -0.84 dBFS | BS.1770 integrated loudness and true peak on the mix (`tools/mix`), then again on the master's own AAC track (`../tools/score_analyze.swift`, sample peak). Music ducks 18 dB under every voice. |
| No empty frames, no dead seconds | ✅ 0 empty / 0 identical of 122 sampled seconds | ✅ 0 empty / 0 identical of 113 sampled seconds | `tools/qa_frames` samples every 1 s; it fails on a frame more than 97% uniform, and flags a second identical to the next. |

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

| t | On screen | Readable size at 1080p | Real product? | Caption |
|---|---|---|---|---|
|   0.0 s | `M1_flyover`: footage H1 | — | people (story) | — |
|   5.0 s | `M5_shopkeeper`: footage H4 | caption 46 px | people (story) | Every month, lenders send the same reminder, at the same time, to everyone. |
|  10.0 s | `P02_vikram_sms`: footage H8, real UI on the phone screen | — | yes (real UI composited) | — |
|  15.0 s | unit chart: 1,262 dots, 23 lit (seed-42 fixed schedule); words “1,262 messages. / 23 replies.” | 150 px | real counts | — |
|  20.0 s | logo lockup; headline “Collections that decide who, when and how.” | 112 px logo, 56–72 px line, 32 px credits; 72 px; caption 46 px | — | Kairos looks at every borrower, every day, and decides. |
|  25.0 s | `P07_laptop_dashboard`: footage H6, real UI on the laptop screen (push in) | — | yes (real UI composited) | — |
|  30.0 s | card: capture `ov` frame `d1_aladdin_tech` | UI 13 px → 32 px, 16 px → 40 px (zoom 2.49); caption 46 px | yes (real capture) | Aladdin, our safety supervisor, checks it against the lender's rules. |
|  35.0 s | card: capture `ov` frame `v11` | UI 13 px → 32 px, 16 px → 40 px (zoom 2.49); caption 46 px | yes (real capture) | No reply on SMS. So it learns, and tries WhatsApp. |
|  40.0 s | `V2_vikram_smiles`: footage H7 | — | people (story) | — |
|  45.0 s | card: capture `ov` frame `v18` | UI 13 px → 32 px, 16 px → 40 px (zoom 2.49) | yes (real capture) | — |
|  50.0 s | `P15_laptop_paid`: footage H6, real UI on the laptop screen | — | yes (real UI composited) | — |
|  55.0 s | `V4_vikram_relief`: footage H7 | — | people (story) | — |
|  60.0 s | card: capture `aarav` frame `approved` | UI 13 px → 42 px, 16 px → 51 px (zoom 3.20); caption 46 px | yes (real capture) | Your team stays in charge. |
|  65.0 s | `P17a_sunita`: footage H4 | — | people (story) | — |
|  70.0 s | `P17_sunita_otp`: footage H8, real UI on the phone screen | caption 46 px | yes (real UI composited) | Kairos: To protect your account, |
|  75.0 s | `P17_sunita_otp`: footage H8, real UI on the phone screen | caption 46 px | yes (real UI composited) | Kairos: Thank you, you are verified. |
|  80.0 s | `P19_sunita_fake`: footage H8, real UI on the phone screen | caption 46 px | yes (real UI composited) | Kairos answers in seconds. But only once it knows it's really them. |
|  85.0 s | card: capture `in` frame `fake_tech` | UI 13 px → 34 px, 16 px → 42 px (zoom 2.60); caption 46 px | yes (real capture) | Kairos: For your security, I can't help with this request in chat. |
|  90.0 s | card: capture `in` frame `fake_tech` | UI 13 px → 34 px, 16 px → 42 px (zoom 2.60); caption 46 px | yes (real capture) | A doctored payment screenshot? It checks with the bank. |
|  95.0 s | `P20c_laptop_console`: footage H6, real UI on the laptop screen | — | yes (real UI composited) | — |
| 100.0 s | `P20d_borrower_b_reply`: footage H8, real UI on the phone screen | — | yes (real UI composited) | — |
| 105.0 s | `P21_sunita_stop`: footage H8, real UI on the phone screen | caption 46 px | yes (real UI composited) | Kairos: You will not receive further automated reminders from us on any channel. |
| 110.0 s | `P22_team`: footage H11 | caption 46 px | people (story) | In our tests, about twice the money back for every rupee. With zero rule breaches. |
| 115.0 s | proof card (≈2×, 0, 49/49); fine print | 150 px numbers, 40/32 px labels; 32 px; caption 46 px | sourced figures | In our tests, about twice the money back for every rupee. With zero rule breaches. |
| 120.0 s | logo lockup | 112 px logo, 56–72 px line, 32 px credits | — | — |

### Technical film

| t | On screen | Readable size at 1080p | Real product? | Caption |
|---|---|---|---|---|
|   0.0 s | `T01_engineer`: footage H13 | — | people (story) | — |
|   5.0 s | headline “How Kairos works.” | 108 px; caption 46 px | — | This is what happens inside Kairos, for one borrower, on one day. |
|  10.0 s | `T03_street`: footage H1 | — | people (story) | — |
|  15.0 s | card: capture `er` frame `d12`; architecture diagram | UI 13 px → 34 px, 16 px → 42 px (zoom 2.60); titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | Each day, every borrower is re-sorted. Every move names its rule. |
|  20.0 s | card: capture `er` frame `d12`; architecture diagram | UI 13 px → 32 px, 16 px → 40 px (zoom 2.48); titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | A Bayesian learner tracks reply chances. |
|  25.0 s | card: capture `er` frame `d12`; tag “Engine Room · SIMULATED · seed 42 · 300 synthetic borrowers · 14 days”; architecture diagram | UI 13 px → 32 px, 16 px → 40 px (zoom 2.48); 32 px tag; titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | On day seven, WhatsApp replies drop. Kairos isn't told. |
|  30.0 s | card: capture `er` frame ``; tag “Engine Room · SIMULATED · seed 42 · 300 synthetic borrowers · 14 days”; architecture diagram | UI 13 px → 32 px, 16 px → 40 px (zoom 2.48); 32 px tag; titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | By the end of day fourteen, that segment has moved to SMS. |
|  35.0 s | `P16b_laptop_held`: footage H6, real UI on the laptop screen; architecture diagram | titles 36 px, subtitles 27 px; caption 46 px | yes (real UI composited) | Aladdin reserves the budget and limits, then checks every rule again at send time. |
|  40.0 s | card: capture `aarav` frame `approved`; architecture diagram | UI 13 px → 30 px, 16 px → 36 px (zoom 2.28); titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | Aladdin reserves the budget and limits, then checks every rule again at send time. |
|  45.0 s | card: capture `sys` frame `trace`; architecture diagram | UI 13 px → 28 px, 16 px → 35 px (zoom 2.16); titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | Delivery and replies come back as webhooks, and duplicates are ignored. |
|  50.0 s | `T07_sunita_call`: footage H4; architecture diagram | titles 36 px, subtitles 27 px; caption 46 px | people (story) | Kairos: Your EMI was due last week. Could you pay by Friday? |
|  55.0 s | card: capture `dv` frame `learning`; architecture diagram | UI 13 px → 32 px, 16 px → 39 px (zoom 2.46); titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | Voice calls run on Sarvam agents. |
|  60.0 s | `T07b_person_reviews`: footage H10; architecture diagram | titles 36 px, subtitles 27 px; caption 46 px | people (story) | Every call comes back with a disposition and a confidence. |
|  65.0 s | `P12_vikram_whatsapp`: footage H8, real UI on the phone screen; architecture diagram | titles 36 px, subtitles 27 px; caption 46 px | yes (real UI composited) | Replies climb a ladder: rules first, then a small model. |
|  70.0 s | card: capture `ov` frame `d3_intent_tech_scrolled`; architecture diagram | UI 13 px → 30 px, 16 px → 36 px (zoom 2.28); titles 36 px, subtitles 27 px; caption 46 px | yes (real capture) | Still unsure? A person decides. |
|  75.0 s | `P20c_laptop_console`: footage H6, real UI on the laptop screen; architecture diagram | titles 36 px, subtitles 27 px; caption 46 px | yes (real UI composited) | Every action still passes Aladdin, and lands in the audit log. |
|  80.0 s | `T10_engineer`: footage H13, real UI on the monitor screen | caption 46 px | yes (real UI composited) | Same data in, same decisions out. And we attack it, load it, and test it. |
|  85.0 s | terminal (real run) | mono 28 px → 29 px; caption 46 px | yes (real run) | Same data in, same decisions out. And we attack it, load it, and test it. |
|  90.0 s | terminal (real run); callout “50,000 atomic decisions in 11.1 scap breaches: 0” | mono 28 px → 29 px; 50 px | yes (real run) | — |
|  95.0 s | terminal (real run) | mono 28 px → 29 px | yes (real run) | — |
| 100.0 s | callout “Built on research.” | 88 px | — | — |
| 105.0 s | `T12_team_claps`: footage H11 | — | people (story) | — |
| 110.0 s | logo lockup | 112 px logo, 56–72 px line, 32 px credits | — | — |

## Honest notes (what is not perfect)

**Footage and composites**
- **Stock people:** every person is Pexels stock footage, cast to the story (Vikram, Asha, Sunita, borrower B, the team). They are actors, not users. Every screen they hold shows the real Kairos UI, composited in post.
- **Screen composites:** phone screens are green-screen keyed and laptop screens are white-screen tracked (`tools/screen_comp.swift`). On close inspection there is a thin edge along some phone bezels. The pale green phone in borrower B's shots (H8_5) needed a lower key threshold and a UI contrast lift. Asha's laptop is H6_3, a woman's hands over the shoulder; her sleeve colour does not match her wide shots.
- **UI inside footage:** phone and laptop screens in footage are scene detail and are not meant to be read at 1080p. The readable component always follows on a cream card.

**Captures**
- **Recorder animations:** the offscreen WKWebView recorder disables page animations. The Engine Room's one-time pulse on the 1–30 card, and its chip flip at the switch, are therefore not in the film. The stills show the final states. All motion comes from the compositor.
- **Real UI wording:** cards show the pages' own wording, for example "Locked by guardrails: no person can override this." and engine times in ms. The approval beat crops the Approvals row before and after the click, so no "presenter" wording appears on screen.
- **Mixed sources:** the Engine Room panels come from a separate local run (seed 42, 300 synthetic borrowers, 14 days). The journey cards come from the K03 demo journeys. They are different SIMULATED runs and are labelled as such.

**Captions never over UI**
- **Moving band:** in phone shots where the phone reaches the bottom of the frame, the caption band moves to the clean top of the shot. Sunita's phone uses top centre; Vikram's phone uses top left. The band jumps position at those cuts.
- **No push-ins under narration:** the laptop push-ins carry no narration. In the technical film, the two laptop shots under narration are wide, so the caption sits on the keyboard.

**Timing and sound**
- **Long phrases:** two narration lines are longer than one caption cue: "Before anything goes out, …" and Kairos's OTP line. They are split at the clause boundary into two complete cues. A few line breaks fall before a conjunction rather than at a comma.
- **Tight dialogue:** in the Sunita exchange, the gap between speakers is 0.17–0.27 s, including the files' own tail silence.
- **Lockup:** the product runtime is 2:01. The music stops on the bar 68 downbeat, and the lockup holds 4.6 s in silence by design. The technical film runs 1:53, and its closing loop animation is shortened to about 2.5 s to land on the hard stop.

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
