# Kairos film: live-action / screen-capture plan

This is the screen-recording alternative to the motion-graphics cut. The frame-accurate shot lists for the
film itself are `shot-list.csv` (full, 216 s) and `shot-list-operator.csv` (89 s). Both pass the skill
pack's `validate_shotlist.py`. Lines and timecodes are in `SCRIPT.md`. All screens show synthetic data, so
keep the SIMULATED / SYNTHETIC badges in frame.

## Already captured (real screens in the film)

Captured on 19 Nov 2026 from `origin/main` (160fb45) with headless Chrome at 1920×1080. They are in
`assets/real/`; the full list is in `TRUTH_TABLE.md`. To re-capture:

```bash
git checkout main && cd backend && source .venv/bin/activate
KAIROS_CM03_DEMO_INBOX=1 KAIROS_DB=/tmp/cm03-demo.db uvicorn kairos_backend.api:app   # :8000
# CM-03 console: http://127.0.0.1:8000/cm03/console
#   B-1042: "Kal EMI pay kar di thi" -> OTP from the right panel -> "Balance kitna hai?" -> "UTR 512345678901"
#   B-2001: hello -> OTP -> "Edited screenshot" button  => FRAUD_SUSPECT hand-off
#   B-1042 (new chat): STOP                              => STOP_HONOURED
python3 scripts/run_journey.py --quick                  # reports/journey/journey.html
cd ../crm && python3 -m kairos.web & python3 -m kairos.replay   # http://127.0.0.1:8042/borrowers/B-1042
```

## Shots to record live

| Film scene (full cut time) | Screen to record | How | What to do on screen |
|---|---|---|---|
| s1 0:00 problem | Team phone receiving the same SMS 5× | Phone-demo mode (OPERATING_GUIDE Demo E; dry-run by default, only the team's own numbers) | Phone flat on the desk, five buzzes |
| s2 0:13 fixed calendar | K09 replay output, CN10 column | `python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 271828 --baseline both` | Zoom on the CN10 lines |
| s3 0:24 Kairos decides | Journey report decision rows | `reports/journey/journey.html` | Scroll to "K09 picked whatsapp 17-19 … ALL_PASSED" |
| s4 0:38 Ravi's journey | Live journey player + Sarvam test chat | Demo UI `/journey` (needs Node, `npm run dev`); Sarvam console → "Kairos - PTP Follow-up" (Ritu) → Test / Chat | Let SMS → chat → voice → pause → verified play; in Sarvam, type "27 tareekh ko kar dunga" and show the read-back |
| s4b 1:07 on the record | CRM borrower page | as above | Zoom on Loan A **EMI CLEARED** / reminder **CANCELED** next to Loan B **PENDING** |
| s5 1:16 safety | CM-03 console + red-team output | console as above; `python3 scripts/run_redteam.py` | OTP first; fake screenshot → FRAUD_SUSPECT; CON-01/02 PASS lines |
| s6 1:32 people in charge | Control Room approvals | Demo UI `/control`, role switch → supervisor | Click **Approve**, scroll two audit rows |
| s7 1:39 it learns | Learning page + replay switches | Demo UI `/learning`; replay "In-campaign channel switches" | Bars moving; zoom on the switch line |
| o1 1:57 morning | Control Room "today" panel | Demo UI `/control` | Slow pan across today's decisions |
| o2 2:09 scoreboard | **`/demo/live`**: the live ₹-recovered race | Branch `demo/live-collections` (being built; not on main at capture time). Once it lands: `uvicorn kairos_backend.api:app` then `/demo/live` | Record 20 s of the race; this replaces the rebuilt scoreboard |
| o3 2:33 one big call | Control Room approvals, hardship item | Demo UI `/control` | Approve the hardship call; show the gate re-check |
| o4 2:47 fingertips | CRM page, journey report, report pack | `python3 scripts/report_pack.py --borrowers 60 --days 3 --seed 42`, open the CSVs | One click each |
| o5 2:59 / s8 3:13 proof | Terminal outputs | K09 replay final seeds; `run_redteam.py`; `run_cm03_eval.py`; `bench_ledger.py 50000` | One zoom per number, with a "SIMULATED data" lower-third |

Recording settings: Chrome at 1920×1080, zoom 125% for text, notifications off, no personal tabs. On a
phone, use Do Not Disturb and only the team's test number. Mux the rendered soundtrack as in README.md.

## 9:16 cut for social (about 45 s)

1. Full cut: s1 hook (phone, already vertical) → s4 journey (phone) → s5 safety cards one at a time → s8 proof as
   three full-screen numbers (~2×, 0 breaches, 73.2%) → lockup. Operator 9:16: o2 scoreboard cropped to RUNS +
   WICKETS + commentary, o3 the one tap, o5 result.
2. Burn the captions in, larger, two lines max, in the lower-middle third; keep the "SIMULATED data" label on every
   number.
3. Re-time with `compose.py` (add a `vertical` cut to `CUTS` in the HTML) so music and voices follow the new edit.
4. End card: "Kairos. Right message. Right time. Or none at all."
