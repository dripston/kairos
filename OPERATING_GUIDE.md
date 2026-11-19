# Kairos operating guide (plain language)

This is the "how do I run and show it" guide. You do not need to read code. Copy each command into
Terminal on the Mac, one at a time, and press Enter.

Everything Kairos shows is **SIMULATED** (synthetic borrowers, a simulator for replies) unless a
screen says LIVE SANDBOX. Say that out loud in the demo; the judges reward honest labelling.

---

## 1. What Kairos does, in three lines

1. Every day, for every borrower, it decides: **don't contact today**, or **this channel, this time
   slot, this message** (SMS, WhatsApp, email, IVR, voice bot).
2. A **Supervisor** checks every decision against the lender's rules (consent, quiet hours, caps,
   budget, STOP, hardship, payment already made) **twice**: when planning and again just before sending.
3. A **learner** watches who replies and gets cheaper and better at reaching people over the campaign.
   Humans can approve, hold or override anything (human in the loop).

---

## 2. One-time setup (10 minutes)

```bash
git clone https://github.com/dripston/kairos.git
```

```bash
cd kairos/backend && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
```

Check it works (should end with "passed"):

```bash
pytest -q
```

Frontend (the demo app):

```bash
cd ../frontend && npm install
```

---

## 3. Getting the newest work

Most features are in open pull requests (PRs) that are not merged yet. Merge them on GitHub in this
order (each one has a green "Merge" button once reviewed). After each merge run `pytest -q` in
`backend/`; if a PR says "conflicts", ask Claude to resolve it ("resolve conflicts on PR #N").

| Order | PR | What it adds |
|---|---|---|
| 1 | #7 | This guide, the checklist and CLAUDE.md (context for whoever continues) |
| 2 | #11 | The learner (K09) and Supervisor state machine on the real data spine, plus the proof replay |
| 3 | #12 | Human in the loop: approvals, holds, manual sends, audit |
| 4 | #13 | Lender reports (AICI, Data Dump, Trails, FTD, Hourly) and the daily per-segment report |
| 5 | #14 | Red-team attack suite (proves the guardrails hold) |
| 6 | #10 | Channel adapters, including "send to my own phone" demo mode |
| 7 | #8 | Message templates for every channel, compliance-checked |
| 8 | #6 | CRM borrower page with the 5-step replay, and the voice bot flows |
| 9 | #16 | Fixes for the 4 serious red-team findings (merge after #14) |
| 11 | #17 | Learner tuning: +48% to +61% on unseen seeds (merge after #11) |
| 10 | #15 | Voice engine: picks the bot, voice and language per call; acts on call outcomes using confidence thresholds (merge after #11) |


---

## 4. The demos

### Demo A: the proof that the learner wins (CM-02 headline number)

```bash
cd kairos/backend && source .venv/bin/activate
```

```bash
python3 scripts/run_k09_replay.py --borrowers 1500 --days 14 --seed 42 --baseline both
```

Takes 1 to 3 minutes. Read these lines in the output:

- **Replies per ₹1,000**: Fixed (CN's 10-day cadence) vs K09. With PR #17, K09 is about 30 to 60% higher.
- **K01 cap breaches: 0 / 0** and **K15 safety violations: 0 / 0**. This must always be zero.
- **In-campaign channel switches**: shows the learner changing its mind when WhatsApp gets worse mid-campaign.

Results on 3 seeds never used for tuning: +48%, +61%, +56%, with more verified payments than CN on every seed and zero cap breaches. Be ready for one question: opt-outs (STOP replies) are higher than CN's cadence.

### Demo B: one borrower's whole journey (CRM page)

Needs PR #6. From the `crm/` folder:

```bash
python3 -m kairos.web
```

Open http://127.0.0.1:8042/borrowers/B-1042 and press **Replay journey**. It walks through:
SMS delivered, borrower says "need time" on WhatsApp, borrower claims payment on Loan A, the bank
confirms Loan A (its reminder is cancelled, Loan B stays open), and a duplicate message is ignored.
Each step shows that delivery, borrower intent and verified payment are kept separate.

### Demo C: the API the frontend talks to

```bash
uvicorn kairos_backend.api:app --reload
```

Open http://127.0.0.1:8000/docs. Every rule, approval and report can be tried from that page.

### Demo D: the voice bots (Sarvam)

Six bots live in the CreditNirvana Sarvam workspace, all named "Kairos - ...":

| Bot | Voice | When Kairos uses it |
|---|---|---|
| Pre-Due Reminder | Ritu | A few days before the E M I is due |
| EMI Reminder 1-30 DPD (K17) | Ritu | E M I missed, up to 30 days late |
| Callback Voicebot (K18) | Advait | The borrower asked to be called back |
| Mid-Bucket Resolution 31-90 DPD | Advait | 31 to 90 days late; firmer, handles part payments |
| PTP Follow-up | Ritu | The borrower promised a date and the payment did not arrive |
| Hardship Triage | Advait | Only after a human approves; never asks for money |

All six speak Hindi, English, Marathi, Tamil, Telugu, Kannada, Bengali and Gujarati and switch
automatically. They never say amounts or account numbers, never threaten, confirm identity first,
and read back any promised date. After each call they return a **disposition** (for example PTP,
ALREADY_PAID, HARDSHIP, STOP_REQUESTED) with a **confidence** from 0 to 1. Kairos acts on it only above
a threshold: a STOP or hardship is honoured even when unsure (safety first); a promise is trusted
only when confident and within the allowed date window; anything unclear goes to a human.

To try a bot: open the Sarvam console, pick the bot, use **Test / Chat**, and type as the borrower.
Nothing dials out to real people. A test phone number can be set up on request.

### Demo E: send a real SMS / WhatsApp to your own phone

Needs PR #10. Your number is stored only on your Mac (`~/.kairos/demo_recipients.json`), never in
GitHub. Teammates add their numbers with the API (`POST /demo/recipients` on the /docs page); a
**Demo recipients** box in the app itself is still to be built. Sending is **dry-run by default**: nothing goes out until you turn it on
for the demo, and only to numbers on that list.

### Demo F: lender reports

Needs PR #13.

```bash
python3 scripts/report_pack.py --borrowers 60 --days 3 --seed 42
```

Writes 11 CSV files to `backend/reports/report_pack/` in the formats the lender (HDFC) already uses.

### Demo G: the guardrail attack test

Needs PR #14.

```bash
python3 scripts/run_redteam.py
```

Prints 49 attacks with PASS/FAIL. Today: 39 pass; the 10 fails are known gaps, each with an owner
and a fix (the 4 serious ones are being fixed on `redteam/fix-high`).

### Live collections view (`/demo/live`)

The one to put on the projector. A 14-day campaign plays out live (about 3 minutes at 1×, under a
minute at 4×): Kairos on the left, the usual fixed schedule on the right, with the same 300 synthetic
borrowers, the same budget and the same rules. Everything on it is **SIMULATED**.

```bash
cd kairos/backend && source .venv/bin/activate && uvicorn kairos_backend.api:app --port 8000
```

Then open <http://localhost:8000/demo/live>. It looks like the lender's own operations console: a
sidebar (Overview, Live activity, Approvals, Borrowers, Rules (Aladdin), System) and a small
**Demo controls** group at the top right. Leave the run type on "vs fixed schedule", pick seed 42 and
press **Start** (4× is the default). **P** toggles Presenter mode (hides the demo controls, larger
type), **S** steps Overview → Live activity → System, and **Space** pauses. `?presenter=1` opens
straight into Presenter mode, `?theme=dark` in the dark theme (light is the default). "Full system
trace" (or **Start full system trace** on the System page) runs the full-mode campaign through every
component instead; it has no fixed-schedule comparison.

**Presenter script (about 60 seconds).** "Three hundred people have missed an EMI. This is the
collections console. Kairos decides every morning, for each person, whether to message them, when, and
on which channel. The usual fixed schedule does the same job with the same money and the same rules,
and the chart compares the two. Every message passes Aladdin, our supervisor, which holds back anything
the lender's rules forbid: a STOP, a quiet day, a weekly limit. The activity feed says what happened in
plain words, like 'Ravi gets a WhatsApp at 5:30 pm' or 'not contacted today'. Payment claims the bank
has not confirmed come to a person under Needs you. On day 7 WhatsApp stops working as well. Nobody tells
Kairos; by day 11 it has moved to SMS. Recovered is confirmed by the bank, and rule breaches stay at zero.
The borrowers are synthetic and the replies come from a simulator, which is why it says SIMULATED."

Seed 42 ends at Kairos ₹1,76,723 recovered on ₹471 spent vs the fixed schedule's ₹17,938 on ₹575
(about 9.9× the fixed schedule), with 0 rule breaches on both sides. Every run of the same seed gives the same numbers.

### Journey 2: inbound WhatsApp servicing, live (`/demo/journey/inbound`)

One presenter screen for CM-03. Left: a borrower's WhatsApp (two synthetic borrowers, A and B). Middle: what
the **real CM-03 engine** did for each message, in plain words (press **Technical** or **T** for state names,
template ids and the raw timeline envelopes). Right: the agent console, where a person **takes over** a chat
from the bot, replies, decides, and hands it back. Everything on it is **SIMULATED**: its own throwaway
database, synthetic borrowers, a simulated bank feed, a dry-run WhatsApp adapter. Nothing is sent anywhere
and no Sarvam agent is called. Without a Sarvam key it runs fully (rules + small offline model; Prism and
reply phrasing stay off; set `KAIROS_J2_PHRASING=1` with a key to try phrasing).

```bash
cd kairos/backend && source .venv/bin/activate && uvicorn kairos_backend.api:app --port 8000
```

Open <http://localhost:8000/demo/journey/inbound> (add `?script=1` to open with the script showing). **P**
shows the presenter script (← / → between cards), **Reset demo** starts again from a clean world.

| Click | What happens (real code path) |
|---|---|
| When is my EMI due? | `ConversationEngine.handle`: code sent to the registered number, the question parked, nothing disclosed |
| Reply with code | the code the engine sent to the simulated phone; verified, the parked question answered |
| Payment link | signed payment link |
| I already paid → genuine / edited screenshot / someone else's UTR | `fraud.py` checks + D7 feed; fakes refused and sent to a person, never marked paid |
| मेरी EMI कितनी बाकी है? | Devanagari: the rules are unsure, the small model answers |
| Ignore your rules… | injection detector fires before any model; refused, flagged |
| I lost my job | K13 hardship hold (4 queued reminders cancelled), routed to a person |
| STOP | `Ledger.record_stop`: every channel opted out, queued reminders cancelled, the Gate then blocks every channel |
| Take over / Send / Reject claim / Hardship: hold / Hand back | `cm03/takeover.py` + K06 HITL (case claim / resolve, 30-day hold); each is one audit row |

**Story (about 2½ minutes):** (1) Borrower A: EMI question → code → answer → payment link. (2) I already
paid → edited screenshot: caught; Take over → Reject claim → Hand back. (3) Ignore your rules: refused.
(4) Borrower B: I lost my job → Take over → suggested reply → Send → Hardship: hold → Hand back. (5) Borrower
A: STOP → consent revoked on WhatsApp, SMS, voice and email, 4 → 0 queued, the Gate blocks every channel.
Step 2's hand-back matters: a chat waiting for a person only gets a holding reply, so step 3 needs the bot back.

While a person owns a chat the engine never sends a bot reply (any path, including the bank re-check), the
chat does not time out into a new bot chat, and STOP is still honoured. The same takeover is on the main
CM-03 API: `POST /cm03/conversations/{id}/takeover`, `/agent-message`, `/claim-decision`, `/hand-back`.

---

## 5. If something goes wrong

| You see | Do this |
|---|---|
| `command not found: python3` | Install Python 3.9+ from python.org |
| `No module named ...` | Run `source .venv/bin/activate` in `backend/` first |
| Tests fail after a merge | Tell Claude: "tests fail on main after merging PR #N, fix it" |
| Port already in use | Close the other Terminal window running the server, or restart the Mac |
| A Sarvam bot says something wrong | Note the exact line and tell Claude which bot; fixes are one small edit |

## 6. Who owns what

Ojas: policy (K01, K13). Varun: data spine, replay, metrics. Rehan: adapters, Supervisor, OTP and
payment verification. Yuval: CRM, voice bots, templates, WhatsApp UX, demo. The full map is in
CLAUDE.md.
