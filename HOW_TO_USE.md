# How to use Kairos

A guide to the deployed app for presenters, teammates and judges. Everything runs on synthetic
borrowers, and every number is **SIMULATED** unless a screen says LIVE SANDBOX or REPLAY. No real
SMS, WhatsApp or call is ever sent from the hosted app: every channel is a mock, unless **live WhatsApp
mode** is switched on for one allow-listed phone (section 7).

- [1. Open it](#1-open-it)
- [2. Before you present (2 minutes)](#2-before-you-present-2-minutes)
- [3. The screen at a glance](#3-the-screen-at-a-glance)
- [4. Pages](#4-pages)
- [5. How a person steps in](#5-how-a-person-steps-in)
- [6. Explain a decision with AI](#6-explain-a-decision-with-ai)
- [7. The WhatsApp servicing demo (CM-03)](#7-the-whatsapp-servicing-demo-cm-03)
- [8. A 7-minute demo script](#8-a-7-minute-demo-script)
- [9. Useful URL options](#9-useful-url-options)
- [10. Health and troubleshooting](#10-health-and-troubleshooting)
- [11. Running it on your own laptop](#11-running-it-on-your-own-laptop)
- [12. How it is deployed](#12-how-it-is-deployed)

---

## 1. Open it

| What | Link |
|---|---|
| **The app** | https://dripston.github.io/kairos/ |
| Backend API (interactive docs) | https://kairos-backend-5ifc.onrender.com/docs |
| Backend health | https://kairos-backend-5ifc.onrender.com/health/details |
| Journey 1 · Outbound collections (presenter page) | https://kairos-backend-5ifc.onrender.com/demo/journey/outbound |
| Journey 2 · Inbound WhatsApp (presenter page) | https://kairos-backend-5ifc.onrender.com/demo/journey/inbound |
| Live collections view | https://kairos-backend-5ifc.onrender.com/demo/live |
| Engine room (churn engine + learning loop) | https://kairos-backend-5ifc.onrender.com/demo/engines |
| WhatsApp servicing console (CM-03) | https://kairos-backend-5ifc.onrender.com/cm03/console |

The app opens on the **Control Room**. If the header says **API live · policy v1.1.1**, the app is
reading the real backend. If it says **API offline**, every panel shows its built-in demo data,
labelled `SIMULATED · offline mock`, and the app still works end to end.

## 2. Before you present (2 minutes)

1. **Wake the backend.** The free hosting plan sleeps after about 15 minutes idle; the first request
   then takes up to a minute. Open https://kairos-backend-5ifc.onrender.com/health and wait for
   `{"ok":true,...}`.
2. **Open the app** and check the header says **API live**.
3. Go to **Control Room → Budget & channels → System health** and press **Test models live**. All
   three AI models should show a green tick.
4. Pick a theme: the sun / moon button at the top right switches light and dark. Light mode reads
   better on most projectors.
5. Use a browser window at least 1366 px wide.
6. **Live collections (`/demo/live`)**: on the free plan a run spends about 38 s on "Preparing" before day 1.
   Open it and press Start a minute before you need it.
7. **Journey pages**: there is one shared session per journey. Open Journey 1 as
   `/demo/journey/outbound?presenter=<any-word>` so nobody else can restart it during the talk, and do not
   share the journey links with the audience while presenting.
8. **CM-03 console**: after a fraud or security hand-off, press **New chat** before the next demo on that phone;
   it closes the handed-off chat on the server (audited).

A restart of the backend resets its data to the same seeded demo state, so every run of the demo
starts from the same place.

## 3. The screen at a glance

**Top bar**, left to right:

- `SIMULATED`: the evidence label for everything on screen.
- **Day 3 / 10 · 09:05** and **Next day**: the demo campaign clock. *Next day* moves the whole
  simulation forward one day (new sends, replies, payments, learning).
- **API live / offline**: whether the app is connected to the backend.
- **Simulation** (`Shift+S`): inject events such as a provider outage, a STOP reply or a payment.
- Sun / moon: light or dark mode.

**Sidebar:**

| Group | Page | Use it to |
|---|---|---|
| Run | Control Room | Watch collections, act on what needs a person, stop anything |
| Run | Smart CRM | Find a borrower and see their whole history |
| Set up | Cohorts | Group borrowers (risk, region, DPD, EMI, bounces…) |
| Set up | Campaigns | Plug campaigns into cohorts; set channels, budget and caps |
| Prove | Learning & Proof | Show that the learning policy beats fixed cadence |
| Demo | Live journey, Voice bots | Story-mode replay of one borrower; the AI voice agents |
| Show | Journey 1 · Outbound | Presenter page: outbound collections running live; pause, approve or reject, override, hold, STOP |
| Show | Journey 2 · WhatsApp | Presenter page: inbound WhatsApp servicing with the borrower's phone, OTP, fraud checks, hand-off |
| Show | Live collections | Kairos vs the fixed schedule, side by side |
| Show | Engine room | The churn engine and the learning loop, live |
| CM-03 | WhatsApp console | The inbound WhatsApp servicing agent (appears when the backend is live) |

**Acting as** (bottom left): Operator, Supervisor or Policy owner. The role decides what you may do;
for example, only a Supervisor or Policy owner can approve a held send, and only the Policy owner can
loosen a cap. It is a demo switch, not a login.

## 4. Pages

### Control Room

Three tabs.

**Overview** is the dashboard, top to bottom:

1. **Five numbers**: money collected, collection %, average collected per day, replies per ₹ vs
   fixed cadence, and cap breaches (always 0).
2. **Money collected**: cumulative ₹ collected by Kairos against the fixed-cadence baseline on the
   same budget and caps. **Today's sends by channel**: where Aladdin is spending today.
3. **Replies per ₹** over time, **collection % by region** and **collection % by cohort**. Click a
   cohort bar to focus the whole dashboard on that cohort.
4. **Needs you** and **How you step in**: see [section 5](#5-how-a-person-steps-in).
5. **Today's plan**: what Aladdin will send, by campaign or cohort. Open a row to see each borrower.
6. **Cohort table**: loans, overdue, collected, response rate, best channel now, attached campaigns.

Use **By cohort / By campaign** and the dropdown (top right) to look at one slice or all of them.

**Live activity** shows the backend directly: Aladdin's decisions today (each with the rule that fired
and a ✨ Explain button), the live event feed, the approvals queue, the CM-03 console summary and the
audit log. **Export CSV** downloads the event log.

**Budget & channels** shows budget and caps per campaign, channel status, demo phone recipients,
**System health**, and the campaign controls (pause, stop, daily budget, weekly contact cap, channels,
learning freeze, which sends need approval).

### Smart CRM

The portfolio: loans, outstanding, DPD, risk tag, region, NACH status, last response, cohort, next
action and status, with filters. Open a borrower to see:

- **Next action / Aladdin's decision** with the guardrail checks and a ✨ **Explain** button.
- **Loans & EMIs**: each EMI separately. A verified payment clears that EMI only.
- **Consent & reach** per channel, **holds and flags**, and the **timeline** of every touch, reply,
  verification and decision (delivery is reach, never a reply).
- **Borrower actions**: pause, resume, hold 2 days, cancel the next touch, hand off to a person,
  assign to me, do not contact. **Manual send preview** runs the guardrails without sending.

### Cohorts

Build a cohort from conditions (risk tag, DPD, region, EMI, bounce string, NACH, last response),
preview how many borrowers match, save it, set its priority, and tick which campaigns run on it. A
borrower belongs to the highest-priority cohort that matches.

### Campaigns

Each campaign card shows its cohorts and links to its Control Room view. **New campaign** walks
through name, audience (cohorts first; uploading a CSV is optional and is checked against the
portfolio-intake rules), channels, budget, schedule and approval settings.

### Learning & Proof

The proof that the learning policy beats fixed cadence on the same seed, budget and caps (REPLAY on
unseen seeds), what the learner believes per band, channel and time slot, when it switched channel,
and learning by **cohort, channel and region**.

## 5. How a person steps in

Aladdin decides routine sends on its own, inside the guardrails. A person can step in at four levels,
from everything down to one borrower:

| Level | Where | What happens |
|---|---|---|
| **Everything** | **Pause all outbound** (Control Room header) | Every channel stops at once. Pending sends are held, not lost. Resume re-checks every guardrail. |
| **A campaign** | How you step in → Pause a campaign, or Budget & channels | Its sends are held and its audience kept. Stop cancels its reservations and releases the budget. |
| **Today's sends** | Today's plan → open a campaign or cohort | Hold, cancel, reschedule +1 day, or switch channel, one row or a selection. |
| **One borrower** | Smart CRM → borrower → Borrower actions | Pause, hold, hand off, do not contact. |

**Needs you** lists the cases where Aladdin stopped and is waiting for a person: sends that need
approval (IVR, hand-offs, high-intensity nudges), hardship and dispute cases, waiver requests and a
cohort that is collecting slowly. Each item has its button (Approve all, Take it, Mark resolved, Focus
on it).

Every action asks for a reason and shows its effect before it applies, and it is written to the
audit log.

**What nobody can override**, in any role: consent, a STOP reply (it stops every channel from the next
cycle), contact caps, quiet hours and registered templates. Guardrails run twice: when a send is
approved and again just before it goes.

## 6. Explain a decision with AI

Press **✨ Explain** on a borrower's decision card, or the ✨ on any row of Aladdin's decisions (Live
activity tab). Kairos sends the recorded audit trail (masked) to DeepSeek and shows a short, plain
explanation, labelled **AI-generated** with the model name and how long it took. For example:

> Kairos decided to send (SEND) at the second guardrail check just before sending (SEND_TIME_CHECK),
> because every guardrail passed (ALL_PASSED) and the borrower was allowed to contact now (ELIGIBLE).

- The text is checked against the recorded facts before it is shown. If the model is slow (it has
  about 6 seconds) or its text fails the check, you see the plain recorded facts instead. Nothing breaks.
- An explanation never changes a decision, a guardrail or a send.
- Where the AI models are used: **sarvam-prism** classifies unclear borrower replies (only when the
  rules are unsure, and only if it is confident and agrees with them); **sarvam-105b-conversations**
  rewords approved WhatsApp replies so they read naturally in English or Hinglish;
  **deepseekv4.1-flash-1017** writes explanations and the daily ops summary. None of them decides a
  send, a guardrail, money or consent.

## 7. The WhatsApp servicing demo (CM-03)

Open the **WhatsApp console** (sidebar, or https://kairos-backend-5ifc.onrender.com/cm03/console) and
play the borrower:

1. Send **hi**. The bot asks for the 6-digit code sent to the registered number. Nothing about the
   account is shared before verification.
2. The console shows the simulated OTP (only for synthetic `+910…` numbers). Send it.
3. Ask in Hinglish, e.g. **kitna baaki hai mera?**: you get the balance per loan.
4. Try **salary late aayi hai, agle hafte pakka bhar dunga**: the bot starts a promise-to-pay and asks
   for a date within 7 days.
5. Try a fake payment claim or a doctored screenshot: it is caught and handed to a person.

### Live WhatsApp (a real phone)

With live mode on, a real phone talks to CM-03 on WhatsApp. Setup is in
[docs/LIVE_WHATSAPP.md](docs/LIVE_WHATSAPP.md). It is off unless `KAIROS_CM03_LIVE_WHATSAPP=1`, `WA_TOKEN`,
`WA_PHONE_NUMBER_ID`, `WA_APP_SECRET` and a one-number `KAIROS_DEMO_ALLOWLIST` are all set on Render.

- The console header shows a green **LIVE WhatsApp · +********XXXX** badge, and a live panel shows the
  phone's chat as it happens, with ✓ / ✓✓ / read ticks.
- Send **Hi** from the allow-listed phone. The code arrives on WhatsApp; type it back. The account
  behind the phone is synthetic Borrower A (`B-2001`, SIMULATED).
- **Next screenshot = edited (SIMULATED OCR)** before the phone sends an image; **Take over (Asha)** and
  **Send as agent**; **STOP** from the phone; **Reset live chat** to run it again (audited).
- Only that one number is ever messaged. Replies go out only within 24 h of the phone's last message
  (`WINDOW_CLOSED` otherwise), at most 1 per second and 60 per hour. **Kill switch: pause outbound** stops
  sends at once.

## 8. A 7-minute demo script

| Min | Show | Say |
|---|---|---|
| 0–1 | Control Room · Overview | "Aladdin runs collections on its own. Top line: ₹ collected vs fixed cadence on the same budget, zero cap breaches." |
| 1–2 | Click a cohort bar; open Today's plan | "Every cohort and region is visible. This is what goes out today, and I can hold any of it." |
| 2–3 | Needs you → Approve all; Pause all outbound → Resume | "It stops and asks when a person is needed. I can stop everything in one click; nothing is lost." |
| 3–4 | Smart CRM → a borrower → ✨ Explain | "Every decision has a reason and a rule, and AI explains it in plain words. It never decides." |
| 4–5 | Learning & Proof | "On unseen seeds it gets 48–61% more replies per rupee than fixed cadence, and it learns per cohort, channel and region." |
| 5–6 | WhatsApp console | "Inbound servicing: OTP first, Hinglish, promises to pay, fakes caught." |
| 6–7 | Budget & channels → System health → Test models live | "Live backend, three hosted AI models (decision, conversation, explanation) answering now. Everything is labelled SIMULATED." |

## 9. Useful URL options

Add these to any app link:

| Option | Effect |
|---|---|
| `?theme=light` / `?theme=dark` | Force a theme (remembered in that browser) |
| `?api=https://host` | Point the app at a different backend (remembered); `?api=default` goes back to the built-in one, `?api=none` runs fully offline on demo data |
| `/control?tab=activity` / `?tab=setup` | Open a Control Room tab directly |
| `/control?scope=cohort&id=C3` | Open the Control Room focused on one cohort |
| `/crm/B-1042` | Open one borrower |

## 10. Health and troubleshooting

| Endpoint | Shows |
|---|---|
| `GET /health` | `{"ok":true,"policy_version":"1.1.1","env":"SIMULATED"}`: is it up (used by the host). `env` is the **evidence label**, not the environment variables: it says SIMULATED because the data is synthetic and every channel is a mock. To see that the environment variables are loaded, check `checks.models.key_source` (`env`) and `checks.database.rows` in `/health/details`. |
| `GET /health/details` | Database (speed, row counts), policy, background loop, AI models (configured, circuit, call stats), uptime, deployed version. Never the key. |
| `GET /health/details?probe=true` | The same, plus one tiny live call to each AI model |

| Problem | Fix |
|---|---|
| Header says **API offline** | The backend is asleep or restarting: open `/health`, wait up to a minute, refresh the app. The app keeps working on demo data meanwhile. |
| Explain shows plain text instead of AI text | The model took longer than ~6 s; press Explain again. |
| A model shows a red cross in System health | The Sarvam key is missing or out of credit (Render → Environment → `SARVAM_API_KEY`). The app works without models. |
| Approve buttons are greyed out | Switch **Acting as** to Supervisor or Policy owner. |
| Numbers look different from yesterday | The backend restarted and re-seeded; that is expected. |

## 11. Running it on your own laptop

```bash
# backend (Python 3.9+), from backend/
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
cp .env.example .env            # optional: add SARVAM_API_KEY to switch the AI models on
python -c "from replay.journey import run; run(seed=42, quick=True, echo=False, db_path='demo_seed.db', cohort_size=40)"
KAIROS_DB=demo_seed.db KAIROS_CM03_DEMO_INBOX=1 uvicorn kairos_backend.api:app --port 8000

# frontend, from frontend/
printf 'VITE_KAIROS_API=http://127.0.0.1:8000\nVITE_KAIROS_CAMPAIGN=CMP-JOURNEY\n' > .env.local
npm install && npm run dev      # http://localhost:5173
```

More demos (the replay proof, red-team suite, lender reports, real SMS to your own phone) are in
[OPERATING_GUIDE.md](OPERATING_GUIDE.md).

## 12. How it is deployed

- **Frontend:** GitHub Pages. CI (`.github/workflows/ci.yml`) runs backend tests on Python 3.9 and
  3.11, the red-team suite, the CRM tests, the frontend build and a Docker smoke test on every push and
  pull request. When CI passes on `main`, `.github/workflows/pages.yml` publishes the app. The repo
  variable `KAIROS_PUBLIC_API` tells it which backend to use.
- **Backend:** Render web service `kairos-backend` (free plan, Singapore), built from `main` with
  `render.yaml` settings. It auto-deploys on every merge to `main` and seeds the demo database at build.
  The Sarvam key lives only in Render's environment settings, never in the repo.
- Details: [DEPLOY_BACKEND.md](DEPLOY_BACKEND.md). What has been built: [PROGRESS.md](PROGRESS.md).
  What is left: open issues #32–#51.
