# Kairos film: live-action / screen-capture shot list

The alternative to the motion-graphics cut (`kairos_film.html`): record the real product screens and lay
the same voice-over (`SCRIPT.md`) on top. Same 9 scenes, same timecodes. Every screen is SIMULATED data;
keep the SIMULATED / SYNTHETIC badges visible in frame.

## Before you record (one time, about 15 minutes)

Use the `integration/all` branch: it has every route the demo UI calls (see `OPERATING_GUIDE.md` §2 for
first-time setup).

```bash
cd kairos && git checkout integration/all && git pull
```

Terminal 1, the API (from `backend/`):

```bash
cd backend && source .venv/bin/activate
KAIROS_CM03_DEMO_INBOX=1 KAIROS_DB=/tmp/cm03-demo.db uvicorn kairos_backend.api:app --reload
```

Terminal 2, the demo UI:

```bash
cd frontend && npm install && npm run dev        # http://localhost:5173
```

Terminal 3, the CRM borrower page (Demo B):

```bash
cd crm && python3 -m kairos.web                  # http://127.0.0.1:8042/borrowers/B-1042
```

Terminal 4, a clean terminal for the proof runs (big font, dark theme, 120 columns).

Recording settings: macOS screen recording (Cmd+Shift+5), browser at 1920×1080, zoom 125% so text reads on
a phone, notifications off, bookmarks bar hidden, no personal tabs. Any phone on screen: Do Not Disturb on,
only the team test number, no contact names.

## Shots

| # | Time | Screen to record | How to get there | What to do on screen | Voice-over (from SCRIPT.md) |
|---|---|---|---|---|---|
| 1 | 0:00–0:13 | Team phone receiving the same SMS five times | Phone-demo mode, Demo E (`OPERATING_GUIDE.md` §4; dry-run is the default, switch it on only for the team's own number on `~/.kairos/demo_recipients.json`). Or reuse the animated scene 1 | Film the phone flat on the desk, five buzzes. Cut on the fifth | "Meet Ravi. His EMI is a few days late…" |
| 2 | 0:13–0:24 | The fixed-cadence arm (CN10) | Terminal 4: `python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --baseline both`; or the Campaigns page `http://localhost:5173/campaigns` | Highlight the "Fixed (CN's 10-day cadence)" lines. Or reuse the animated calendar | "Most collections still run on a fixed calendar…" |
| 3 | 0:24–0:38 | Control Room decision feed, then the CRM "Next action" card | `http://localhost:5173/control`; then `http://localhost:5173/crm` and open a borrower | Let the live feed scroll for 3 s; click one decision row to open it (channel, slot, template, the checks). Cut to the borrower's "Next action" card | "Kairos changes that. Every day, for every borrower…" |
| 4 | 0:38–1:00 | Live journey player + CRM replay + Sarvam test chat | `http://localhost:5173/journey` → **Run the journey** (or generate it first: `python3 scripts/run_journey.py --quick --export-frontend`). Then `http://127.0.0.1:8042/borrowers/B-1042` → **Replay journey**. Voice: Sarvam console → bot "Kairos - PTP Follow-up" (Ritu) → Test / Chat | Journey: let it play SMS → WhatsApp → "need time" → voice → pause → verified. CRM: zoom on Loan A **CLEARED** / reminder **CANCELED** next to Loan B **OPEN** / **PENDING**. Sarvam: type as the borrower "27 tareekh ko kar dunga", show the bot reading the date back | "Ravi read an SMS but didn't reply…" |
| 5 | 1:00–1:13 | CM-03 WhatsApp console; red-team STOP lines; CRM handoff | `http://127.0.0.1:8000/cm03/console`. Terminal 4: `python3 scripts/run_redteam.py`. CRM B-1042 after the hardship step | Console: B-1042 types "Kal EMI pay kar di thi" → OTP asked first → "UTR 512345678901" → receipt. Then a fake UTR → flagged. Terminal: zoom on the CON-01 / CON-02 PASS lines (STOP on one channel stops all). CRM: the orange "HANDOFF to a supervisor case" card | "Safety comes first. Reply STOP on one channel…" |
| 6 | 1:13–1:20 | Control Room approvals and audit | `http://localhost:5173/control`, role switch → **supervisor** | Click **Approve** on a pending approval, then scroll the audit list two rows | "And the lender's team can approve, hold or override anything…" |
| 7 | 1:20–1:38 | Learning page; replay channel switches; cohort and layer view | `http://localhost:5173/learning`. Terminal 4: `python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --journeys both` | Learning: bars moving per segment. Terminal: zoom on "In-campaign channel switches". Cohorts/layers: the K27 churn API (`/churn/*` on `/docs`) or reuse the animated lanes | "Kairos learns from every reply…" |
| 8 | 1:38–1:53 | Proof outputs | Terminal 4: the K09 replay above (replies per ₹1,000 and "K01 cap breaches: 0 / 0"); `python3 scripts/run_redteam.py` summary (49/49); `python3 scripts/run_cm03_eval.py` summary; `python3 scripts/bench_ledger.py 50000` | One zoom per number, 2 s each. Put a "SIMULATED data" lower-third on every shot | "Against the lender's own schedule…" |
| 9 | 1:53–2:00 | Title card | Record scene 9 of `kairos_film.html` (`?scene=9`) | Hold | "Kairos. Right message. Right time. Or none at all." |

Notes:

- Never show a real phone number, contact name or the PL_Dummy file. Phone numbers are masked in the UI;
  crop the phone's status bar if it shows a carrier name.
- If a live run is slow, record the terminal output once and speed it up in the edit; say "replay" on screen.
- The motion-graphics scenes can stand in for any shot that is not ready (scenes 1, 2 and 7 are the most
  likely).

## 9:16 cut for social (about 45 s)

1. Scenes 1, 4, 5 and 8 only (problem, Ravi's journey, safety, proof), then the close: about 45 s.
2. Phone-first framing: crop to the phone in scenes 1 and 4 (it is already vertical), stack the three safety
   cards one at a time, and show the proof as three full-screen numbers (~+50%, 0 breaches, 50,000 decisions).
3. Burn the captions in, larger (two lines max, centred in the lower third), with the "SIMULATED data" label
   on every number.
4. End card: "Kairos. Right message. Right time. Or none at all." with the team name.
