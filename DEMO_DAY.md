# Demo day: run sheet (Fri 20 Nov 2026, presentation 17:30 IST)

Everything here is the deployed system. All borrowers, payments and replies are **SIMULATED**; every channel is a
mock or dry-run, except the opt-in live WhatsApp mode, which only ever messages one allow-listed number.

## Live URLs

| What | URL |
|---|---|
| Demo site (React, connected to the backend) | https://dripston.github.io/kairos/control |
| Backend health | https://kairos-backend-5ifc.onrender.com/health |
| Live dashboard (Kairos vs fixed schedule, full system trace) | https://kairos-backend-5ifc.onrender.com/demo/live |
| Engine Room (churn engine + learning loop, live) | https://kairos-backend-5ifc.onrender.com/demo/engines |
| Journey 1: outbound collections, with take-control | https://kairos-backend-5ifc.onrender.com/demo/journey/outbound?presenter=YOURWORD |
| Journey 2: inbound WhatsApp, with take-over | https://kairos-backend-5ifc.onrender.com/demo/journey/inbound |
| CM-03 console (incl. live WhatsApp) | https://kairos-backend-5ifc.onrender.com/cm03/console |
| API docs | https://kairos-backend-5ifc.onrender.com/docs |

## 30 minutes before

1. Open `/health` (the free Render instance takes about a minute to wake).
2. Open `/demo/live` and press Start once (about 38 s on "Preparing"), and `/demo/engines` (about 10 s first load).
3. Open Journey 1 **with `?presenter=YOURWORD`**. For 30 minutes nobody else can start or close your session.
4. Journey 2: press **Reset demo**. CM-03 console: press **New chat** on each phone.
5. Live WhatsApp (if configured, see below): open `/cm03/console`, check the green **LIVE WhatsApp** badge, then send
   **"Hi"** from the allow-listed phone. Replies are only allowed within 24 h of your last message.
6. Have the backup recordings and the two films open locally, in case anything misbehaves.

## Suggested flow (7 minutes)

1. Product film (about 3:30): the whole product story, CM-02 + CM-03.
2. Live, about 2:30, any two of:
   - the Engine Room jumping to `?at=6.05` (the drift) and `?at=13.98` (the switch)
   - Journey 1 at 20:00, SMS → **Blocked by Aladdin**
   - live WhatsApp on the phone: "Hi" → OTP → answer → STOP
3. Close on results. The technical film is for Q&A, or if asked "how does it work".

Press **T** for the Technical view on the journey pages and **P** for presenter cue cards.

## Do / don't

- **Do not push to `main` during the talk.** Render redeploys, the demo data resets and the backend restarts.
- Don't share the journey or `/demo/live` URLs with the audience: each is a single shared session.
- The Control Room **Overview** tab and its **"Needs you"** card run on in-browser simulation. Show live data from
  **Live activity**, **Smart CRM**, the **journeys**, the **Engine Room** and the **CM-03 console** instead.
- Say **SIMULATED** for every number. Use the exact claims in `PROGRESS.md` / `PROBLEM_STATEMENT_CHECKLIST.md`.
- Never paste tokens or keys into chat, issues or PRs. Secrets live only in Render → Environment or a local `.env`.
- Manual-send previews: use dates from 23 Nov onwards (earlier dates are outside the campaign period).

## Live WhatsApp (CM-03 on a real phone)

Off by default. To switch it on, follow [`docs/LIVE_WHATSAPP.md`](docs/LIVE_WHATSAPP.md). In short:
- Render → Environment: `KAIROS_CM03_LIVE_WHATSAPP=1`, `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`,
  `WA_VERIFY_TOKEN`, `KAIROS_DEMO_ALLOWLIST` (exactly one number).
- Meta: point the number's webhook at `https://kairos-backend-5ifc.onrender.com/cm03/whatsapp/inbound`
  (per-number override recommended), using the same verify token, and subscribe to `messages`.
- Check `GET /cm03/live/status`: `live.enabled` should be `true`.
- Before sending a screenshot from the phone, press **"Next screenshot = edited (SIMULATED OCR)"** in the console.
  Real images aren't OCR'd. Use **Reset live chat** to run the story again.

## Where things are documented

| Need | Doc |
|---|---|
| What was built, results, open items | [`PROGRESS.md`](PROGRESS.md), [`PROBLEM_STATEMENT_CHECKLIST.md`](PROBLEM_STATEMENT_CHECKLIST.md) |
| How to use the demo pages | [`HOW_TO_USE.md`](HOW_TO_USE.md), [`OPERATING_GUIDE.md`](OPERATING_GUIDE.md) |
| Deploying the backend, env vars | [`DEPLOY_BACKEND.md`](DEPLOY_BACKEND.md), [`backend/.env.example`](backend/.env.example) |
| Live WhatsApp setup | [`docs/LIVE_WHATSAPP.md`](docs/LIVE_WHATSAPP.md) |
| State machines, policy coverage | [`STATES.md`](STATES.md), [`POLICY_COVERAGE.md`](POLICY_COVERAGE.md) |
| Research lineage | [`backend/README.md`](backend/README.md) → Research lineage |
